/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 3 drive facts, in plain sentences. Everything here is derived from
 * Anaconda's device tree (blivet) and its existing-system scan; nothing is
 * invented. The installer medium is identified by the Atlas probe and is
 * never offered as a target.
 */

import { formatSize, joinList } from "./format.js";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

export const descendants = (devices, id) => {
    const children = v(devices[id]?.children) || [];
    return children.reduce((acc, child) => [...acc, child, ...descendants(devices, child)], []);
};

export const ancestors = (devices, id) => {
    const parents = v(devices[id]?.parents) || [];
    return parents.reduce((acc, parent) => [...acc, parent, ...ancestors(devices, parent)], []);
};

/** "usb", "removable" or "internal", from the bus udev reported. */
export const driveKind = (device) => {
    const attrs = v(device?.attrs) || {};
    const bus = String(attrs.bus || "").toLowerCase();
    if (bus === "usb") {
        return "usb";
    }
    if (v(device?.removable) === true || v(device?.removable) === "True") {
        return "removable";
    }
    return "internal";
};

const KIND_NAMES = {
    internal: "Internal drive",
    removable: "Removable drive",
    usb: "USB drive",
};

export const driveModel = (device) => {
    const attrs = v(device?.attrs) || {};
    const model = String(attrs.model || "").replace(/_/g, " ").trim();
    const vendor = String(attrs.vendor || "").trim();
    const description = String(v(device?.description) || "").trim();
    if (model) {
        return vendor && !model.toLowerCase().startsWith(vendor.toLowerCase()) && !/^ata$/i.test(vendor)
            ? `${vendor} ${model}`
            : model;
    }
    return description || v(device?.name) || "";
};

export const driveTotal = (device) => Number(v(device?.total) ?? v(device?.size) ?? 0);

export const driveTitle = (device) => `${KIND_NAMES[driveKind(device)]} · ${formatSize(driveTotal(device))}`;

/** Operating systems Anaconda found with any part on this drive. */
export const systemsOnDrive = (devices, existingSystems, disk) => {
    const onDisk = new Set([disk, ...descendants(devices, disk)]);
    return (existingSystems || []).filter(system => (v(system.devices) || []).some(device => onDisk.has(device)));
};

export const hasBitLocker = (devices, disk) => descendants(devices, disk)
        .some(id => v(devices[id]?.formatData?.type) === "bitlocker");

export const usedBytes = (device) => {
    const total = driveTotal(device);
    const free = Number(v(device?.free) ?? total);
    return Math.max(0, total - free);
};

/** Fraction of the drive in use, 0..1, for the capacity bar. */
export const usedFraction = (device) => {
    const total = driveTotal(device);
    return total > 0 ? Math.min(1, usedBytes(device) / total) : 0;
};

const systemNames = (systems) => [...new Set(systems.map(system => v(system["os-name"])).filter(Boolean))];

/**
 * The sentence under a drive's title:
 *   "Empty · Samsung 990 PRO"
 *   "Windows 11 is on this drive · 310 GB used"
 *   "SanDisk Ultra · this is your installer"
 */
export const driveSentence = ({ devices, disk, existingSystems, isInstaller }) => {
    const device = devices[disk];
    const model = driveModel(device);

    if (isInstaller) {
        return model ? `${model} · this is your installer` : "This is your installer";
    }

    const systems = systemNames(systemsOnDrive(devices, existingSystems, disk));
    const used = usedBytes(device);
    // Anaconda reports free space as space outside partitions, so this is
    // what partitions take up, not what files take up. Say exactly that.
    const usedText = `${formatSize(used)} in partitions`;

    if (hasBitLocker(devices, disk)) {
        const who = systems.length ? joinList(systems) : "Windows";
        return `${who} ${systems.length > 1 ? "are" : "is"} on this drive, locked with BitLocker · ${usedText}`;
    }

    if (systems.length) {
        return `${joinList(systems)} ${systems.length > 1 ? "are" : "is"} on this drive · ${usedText}`;
    }

    const hasContent = descendants(devices, disk).length > 0 && used >= Math.min(driveTotal(device) * 0.001, 64 * 1024 * 1024);
    if (!hasContent) {
        return model ? `Empty · ${model}` : "Empty";
    }

    return model ? `${usedText} · ${model}` : usedText;
};

/**
 * Rows for the drive list. Usable disks come from Anaconda; installer disks
 * come from the probe and are shown, disabled, so the person can see that the
 * stick they booted from is known and safe.
 */
export const driveRows = ({ devices, existingSystems, installerDisks = [], requiredSize = 0, usableDisks = [] }) => {
    const rows = usableDisks
            .filter(disk => devices[disk] && !installerDisks.includes(disk))
            .map(disk => ({
                bitlocker: hasBitLocker(devices, disk),
                disk,
                disabledReason: requiredSize && driveTotal(devices[disk]) < requiredSize
                    ? `Too small. Luma needs about ${formatSize(requiredSize)}.`
                    : null,
                isInstaller: false,
                kind: driveKind(devices[disk]),
                sentence: driveSentence({ devices, disk, existingSystems, isInstaller: false }),
                systems: systemNames(systemsOnDrive(devices, existingSystems, disk)),
                title: driveTitle(devices[disk]),
                total: driveTotal(devices[disk]),
                used: usedFraction(devices[disk]),
            }));

    installerDisks.filter(disk => devices[disk]).forEach(disk => rows.push({
        bitlocker: false,
        disk,
        disabledReason: "this is your installer",
        isInstaller: true,
        kind: driveKind(devices[disk]),
        sentence: driveSentence({ devices, disk, existingSystems, isInstaller: true }),
        systems: [],
        title: driveTitle(devices[disk]),
        total: driveTotal(devices[disk]),
        used: null,
    }));

    return rows;
};

/**
 * Keep what was learned about every drive. After Anaconda resets a plan its
 * device tree holds only the selected drives, so the other drives, and the
 * systems on them, would otherwise vanish from step 3 and from the review
 * warning that must name them. The first complete view of a drive wins;
 * a later tree may be a plan, not the drive as it is. Looking for drives again
 * starts from nothing.
 */
export const mergeDriveFacts = (previous, { devices = {}, disks = [], existingSystems = [] }) => {
    const facts = {
        devices: { ...(previous?.devices || {}) },
        existingSystems: [...(previous?.existingSystems || [])],
    };
    disks.filter(disk => devices[disk]).forEach(disk => {
        if (previous?.devices?.[disk]) {
            return;
        }
        [disk, ...descendants(devices, disk)].forEach(id => {
            if (devices[id]) {
                facts.devices[id] = devices[id];
            }
        });
    });
    existingSystems.forEach(system => {
        const key = `${v(system["os-name"])}|${(v(system.devices) || []).join(",")}`;
        if (!facts.existingSystems.some(known => `${v(known["os-name"])}|${(v(known.devices) || []).join(",")}` === key)) {
            facts.existingSystems.push(system);
        }
    });
    return facts;
};

/** §7 whole-list states, or null when there is something to choose. */
export const driveListProblem = ({ rows, requiredSize }) => {
    const targets = rows.filter(row => !row.isInstaller);
    if (targets.length === 0) {
        return rows.some(row => row.isInstaller)
            ? { kind: "only-installer", text: "The only drive here is the installer itself. Add another drive to install Luma on." }
            : { kind: "none", text: "Luma cannot find a drive to install on. Check that the drive is connected, then look again." };
    }
    if (requiredSize && targets.every(row => row.total < requiredSize)) {
        const largest = Math.max(...targets.map(row => row.total));
        return {
            kind: "too-small",
            text: `Luma needs about ${formatSize(requiredSize)}; the largest drive here is ${formatSize(largest)}.`,
        };
    }
    return null;
};
