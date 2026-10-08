// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

struct Provisioner {
    let paths: EmulatorPaths
    let output: Output
    let client: ControlClient
    let bootstrap: Bootstrap
    let spawn: (String, Mode, Viewport, Bool) throws -> Void
    let resetTrust: () -> Void
    let send: (String, [String: JSONValue]) throws -> JSONValue

    static let factoryMachine = "__factory"

    /// Builds the factory image by provisioning a throwaway machine and then
    /// promoting its disk. The image the developer's machines clone from is
    /// therefore always the product of this recipe, never of hand edits made in
    /// a running guest.
    func run(force: Bool, bundle: LumaBundle?) throws {
        let manager = FileManager.default
        let imageDirectory = paths.image(Bootstrap.base.id)
        let factoryDisk = imageDirectory.appendingPathComponent("disk.raw")
        guard manager.fileExists(atPath: factoryDisk.path) else {
            throw EmulatorError.notProvisioned("run `luma-emulator bootstrap` first")
        }
        if client.isListening() {
            throw EmulatorError.conflict(
                "stop the running guest first; provisioning needs the control socket"
            )
        }

        let recipe = try EmulatorResources.provisionDirectory()
        let workDirectory = paths.machine(Self.factoryMachine)
        let workDisk = workDirectory.appendingPathComponent("disk.raw")

        // Installing two shells and a toolkit takes tens of minutes. If a
        // previous run got that far and then failed on the way out, resume from
        // its disk rather than making the developer pay for it twice. `--force`
        // always starts from the verified base.
        let resuming = !force && manager.fileExists(atPath: workDisk.path)
        if resuming {
            output.note("Found an unfinished factory guest; checking whether its recipe completed…")
        } else {
            try? manager.removeItem(at: workDirectory)
            try manager.createDirectory(at: workDirectory, withIntermediateDirectories: true)
            resetTrust()
            output.note("Cloning the verified base disk for provisioning…")
            try bootstrap.cloneDisk(from: factoryDisk, to: workDisk)
            let seed = imageDirectory.appendingPathComponent("seed.iso")
            if manager.fileExists(atPath: seed.path) {
                try bootstrap.cloneDisk(
                    from: seed, to: workDirectory.appendingPathComponent("seed.iso")
                )
            }
        }

        output.note("Booting the factory guest headlessly…")
        try spawn(Self.factoryMachine, .desktop, Viewport(width: 1280, height: 800, scale: 1), true)
        _ = try send("await-ready", ["timeout": .int(420)])
        output.note("Guest is up.")

        if let bundle {
            output.note("Admitting the Luma package bundle \(bundle.manifest.bundleName)…")
            output.note("  \(bundle.manifest.packageCount) packages, \(bundle.manifest.completeness), source \(bundle.manifest.sourceCommit.prefix(12))")
            _ = try send("exec", [
                "command": .string("rm -rf /var/tmp/luma-emulator-bundle && mkdir -p /var/tmp/luma-emulator-bundle"),
                "timeout": .int(120),
            ])
            _ = try send("push-file", [
                "local": .string(bundle.url.path),
                "remoteDirectory": .string("/var/tmp/luma-emulator-bundle"),
            ])
        } else {
            output.note("No Luma bundle supplied: this image will be a Fedora substrate, not Luma.")
        }

        output.note("Applying the provisioning recipe (this takes several minutes)…")

        // The recipe writes this inventory as its last real step, so its
        // presence is the guest's own evidence that provisioning finished.
        // Compared for equality, not containment: "incomplete" contains
        // "complete", and a substring test here silently promotes an
        // unprovisioned disk to the factory image.
        let probe = ((try? send("exec", [
            "command": .string(
                "test -s /etc/luma-emulator/packages.txt && echo yes || echo no"
            ),
            "timeout": .int(60),
        ]))?.objectValue?["stdout"]?.stringValue ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let alreadyProvisioned = probe == "yes"

        if alreadyProvisioned {
            output.note("The recipe had already completed in this guest; promoting it as it stands.")
            try finish(
                workDirectory: workDirectory, imageDirectory: imageDirectory,
                factoryDisk: factoryDisk, bundle: bundle
            )
            return
        }

        _ = try send("push-file", [
            "local": .string(recipe.path),
            "remoteDirectory": .string("/tmp/luma-emulator-provision"),
        ])

        // The recipe is started detached and then polled, rather than run over a
        // held-open connection. Installing two shells and a toolkit takes long
        // enough that the guest will stop answering ssh at some point during the
        // rpm transaction, and a command tied to that connection dies with it —
        // half-installed, which is the worst possible state to be in.
        _ = try send("exec", [
            "command": .string("""
            rm -f /tmp/luma-provision.status /tmp/luma-provision.log
            setsid nohup sudo -n bash -c \
              'bash /tmp/luma-emulator-provision/provision.sh > /tmp/luma-provision.log 2>&1; \
               echo $? > /tmp/luma-provision.status' \
              </dev/null >/dev/null 2>&1 &
            sleep 1
            echo started
            """),
            "timeout": .int(120),
        ])

        var provisionStatus: Int?
        var lastNote = ""
        let deadline = Date().addingTimeInterval(3600)
        while Date() < deadline {
            sleep(15)
            guard let poll = try? send("exec", [
                "command": .string(
                    "cat /tmp/luma-provision.status 2>/dev/null || echo running; "
                    + "tail -1 /tmp/luma-provision.log 2>/dev/null"
                ),
                "timeout": .int(60),
            ]) else {
                // A failed poll means the guest is busy, not that it has died.
                continue
            }
            let text = (poll.objectValue?["stdout"]?.stringValue ?? "")
                .split(separator: "\n").map(String.init)
            guard let first = text.first else { continue }
            if first != "running", let code = Int(first.trimmingCharacters(in: .whitespaces)) {
                provisionStatus = code
                break
            }
            if text.count > 1, text[1] != lastNote, !text[1].isEmpty {
                lastNote = text[1]
                output.note("  guest: \(lastNote)")
            }
        }

        let log = ((try? send("exec", [
            "command": .string("tail -500 /tmp/luma-provision.log"), "timeout": .int(120),
        ]))?.objectValue?["stdout"]?.stringValue) ?? ""
        let provisionLog = paths.logs.appendingPathComponent("provision.log")
        try? log.write(to: provisionLog, atomically: true, encoding: .utf8)

        guard provisionStatus == 0 else {
            // Leave nothing running. A failed provision that keeps the factory
            // guest alive holds the control socket, and the next attempt is
            // refused for a reason that has nothing to do with the failure.
            _ = try? send("stop", ["force": .bool(true)])
            var settling = 0
            while client.isListening() && settling < 40 {
                usleep(500_000)
                settling += 1
            }
            throw EmulatorError.failure(
                "provision-failed",
                (provisionStatus == nil
                    ? "provisioning did not finish within an hour."
                    : "provisioning failed with status \(provisionStatus!).")
                    + " The full output is at \(provisionLog.path)\n"
                    + log.split(separator: "\n").suffix(25).joined(separator: "\n")
            )
        }
        output.note("Recipe applied.")
        try finish(
            workDirectory: workDirectory, imageDirectory: imageDirectory,
            factoryDisk: factoryDisk, bundle: bundle
        )
    }

    /// Shuts the factory guest down cleanly, then promotes its disk.
    ///
    /// A disk copied out from under a running kernel is not a factory image, so
    /// promotion happens only after the guest has actually stopped.
    private func finish(
        workDirectory: URL, imageDirectory: URL, factoryDisk: URL, bundle: LumaBundle?
    ) throws {
        let manager = FileManager.default
        output.note("Recording the package inventory…")
        let packages = (try? send("exec", [
            "command": .string("cat /etc/luma-emulator/packages.txt"), "timeout": .int(120),
        ]))?.objectValue?["stdout"]?.stringValue ?? ""
        let missing = (try? send("exec", [
            "command": .string("cat /etc/luma-emulator/missing-packages.txt 2>/dev/null"),
            "timeout": .int(60),
        ]))?.objectValue?["stdout"]?.stringValue ?? ""
        // Read the Luma inventory back from the guest rather than assuming the
        // bundle installed: the manifest must record what is actually there.
        let lumaInstalled = (try? send("exec", [
            "command": .string("cat /etc/luma-emulator/luma-packages.txt 2>/dev/null"),
            "timeout": .int(60),
        ]))?.objectValue?["stdout"]?.stringValue ?? ""
        let completeness = ((try? send("exec", [
            "command": .string("cat /etc/luma-emulator/luma-completeness.txt 2>/dev/null"),
            "timeout": .int(60),
        ]))?.objectValue?["stdout"]?.stringValue ?? "fedora-fallback")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        output.note("Shutting the factory guest down cleanly…")
        // The runtime exits with its guest, so losing the connection here is the
        // expected outcome rather than a failure.
        _ = try? send("stop", ["force": .bool(false)])
        var waited = 0
        while client.isListening() && waited < 180 {
            usleep(500_000)
            waited += 1
        }
        guard !client.isListening() else {
            throw EmulatorError.timedOut(
                "the factory guest did not shut down; refusing to promote a disk from a running kernel"
            )
        }

        output.note("Promoting the provisioned disk to the factory image…")
        try paths.assertOwned(factoryDisk)
        try? manager.removeItem(at: factoryDisk)
        try manager.moveItem(at: workDirectory.appendingPathComponent("disk.raw"), to: factoryDisk)
        try paths.assertOwned(workDirectory)
        try? manager.removeItem(at: workDirectory)

        let inventory = packages.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let absent = missing.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let manifestURL = imageDirectory.appendingPathComponent("manifest.json")
        var manifest = try? JSONDecoder().decode(
            ImageManifest.self, from: Data(contentsOf: manifestURL)
        )
        manifest?.fedoraPackages = inventory
        // Luma's own packages are recorded separately so `status` can say plainly
        // whether a guest is running Luma's downstream shells or Fedora's stock
        // ones. An empty list here is a fact, not an oversight.
        // The Luma inventory comes from what the guest reports it installed,
        // not from filtering the Fedora inventory by name.
        let lumaNEVRAs = lumaInstalled.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        manifest?.lumaPackages = lumaNEVRAs
        if let bundle, !lumaNEVRAs.isEmpty {
            manifest?.lumaBundle = LumaBundleRecord(
                bundleName: bundle.manifest.bundleName,
                bundleSHA256: bundle.sha256,
                sourceCommit: bundle.manifest.sourceCommit,
                sourceBranch: bundle.manifest.sourceBranch,
                completeness: completeness.isEmpty ? bundle.manifest.completeness : completeness,
                packageCount: lumaNEVRAs.count,
                substitutions: bundle.manifest.substitutions.map {
                    "\($0.package): pinned \($0.pinnedRelease), using \($0.usedRelease)"
                },
                absentOptional: bundle.manifest.absentOptional
            )
        } else {
            manifest?.lumaBundle = nil
        }
        manifest?.diskBytes = Shell.fileSize(factoryDisk.path)
        manifest?.createdAt = ISO8601DateFormatter().string(from: Date())
        if let manifest {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(manifest).write(to: manifestURL)
        }

        output.emit(.object([
            "imageID": .string(Bootstrap.base.id),
            "factoryDisk": .string(factoryDisk.path),
            "packagesRecorded": .int(inventory.count),
            "lumaPackagesInImage": .int(manifest?.lumaPackages.count ?? 0),
            "lumaCompleteness": .string(manifest?.lumaCompleteness ?? "fedora-fallback"),
            "packagesFedoraDoesNotCarry": .strings(absent),
        ])) {
            var lines = [
                "Factory image provisioned.",
                "  disk      \(factoryDisk.path)",
                "  packages  \(inventory.count) recorded in the manifest",
                "  Luma      \(manifest?.lumaPackages.count ?? 0) packages, \(manifest?.lumaCompleteness ?? "fedora-fallback")",
            ]
            if !absent.isEmpty {
                lines.append("  missing   Fedora does not carry: \(absent.joined(separator: ", "))")
            }
            return lines.joined(separator: "\n")
        }
    }
}