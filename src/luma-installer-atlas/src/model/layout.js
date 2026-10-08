/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * The review step's Layout line, generated from the storage plan Anaconda
 * will execute: the mount points of the applied partitioning's device tree.
 * Nothing here assumes Fedora's defaults. btrfs subvolumes are named as
 * blivet named them, a swap partition appears only when one is planned, and
 * zram is mentioned only when the payload itself configures it.
 */

import { formatBinarySize as formatSize, joinList } from "./format.js";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

const MOUNT_RANK = ["/", "/usr", "/home", "/var", "/boot", "/boot/efi"];

const rank = (mount) => {
    const index = MOUNT_RANK.indexOf(mount);
    return index === -1 ? MOUNT_RANK.length : index;
};

const byMount = (a, b) => rank(a) - rank(b) || a.localeCompare(b);

const deviceType = (devices, id) => v(devices[id]?.type);
const formatType = (devices, id) => v(devices[id]?.formatData?.type);
const ancestorsOf = (devices, id) => (v(devices[id]?.parents) || [])
        .reduce((acc, parent) => [...acc, parent, ...ancestorsOf(devices, parent)], []);
const deviceSize = (devices, id) => Number(v(devices[id]?.size) || 0);

/**
 * @param {Object} mountPoints  { "/": deviceId, ... } from DeviceTree.Viewer.GetMountPoints
 * @param {Object} devices      planned device data keyed by device id
 * @param {Array|null} selectedDisks  limit swap/BIOS boot rows to these disks
 * @param {boolean|null} zram   true when the payload ships a zram-generator config
 * @returns {{ parts: Array<{text: string, kind: string}>, text: string }}
 */
export const layoutParts = ({ devices = {}, mountPoints = {}, selectedDisks = null, zram = null }) => {
    const groups = new Map();
    const onSelectedDisks = (id) => !selectedDisks || ancestorsOf(devices, id).some(ancestor => selectedDisks.includes(ancestor));

    Object.entries(mountPoints)
            .filter(([mount, id]) => mount && devices[id] && onSelectedDisks(id))
            .forEach(([mount, id]) => {
                const isSubvolume = deviceType(devices, id) === "btrfs subvolume";
                const volume = isSubvolume ? (v(devices[id].parents) || [])[0] : id;
                const key = volume || id;
                if (!groups.has(key)) {
                    groups.set(key, { btrfs: isSubvolume || formatType(devices, id) === "btrfs", id: key, mounts: [], subvolumes: [] });
                }
                const group = groups.get(key);
                group.mounts.push(mount);
                if (isSubvolume) {
                    group.subvolumes.push({ mount, name: v(devices[id].name) });
                }
            });

    const parts = [...groups.values()]
            .map(group => ({ ...group, mounts: group.mounts.sort(byMount) }))
            .sort((a, b) => byMount(a.mounts[0], b.mounts[0]))
            .map(group => {
                const size = formatSize(deviceSize(devices, group.id));
                if (group.subvolumes.length) {
                    const names = group.subvolumes
                            .sort((a, b) => byMount(a.mount, b.mount))
                            .map(subvolume => subvolume.name)
                            .filter(Boolean);
                    const subvolumeText = names.length === 1 ? `subvolume ${names[0]}` : `subvolumes ${joinList(names)}`;
                    return { kind: "btrfs", text: `${joinList(group.mounts)} on btrfs ${size} (${subvolumeText})` };
                }
                return { kind: "mount", text: `${group.mounts.join(" and ")} ${size}` };
            });

    Object.keys(devices)
            .filter(id => formatType(devices, id) === "biosboot" && onSelectedDisks(id))
            .forEach(id => parts.push({ kind: "biosboot", text: `BIOS boot ${formatSize(deviceSize(devices, id))}` }));

    Object.keys(devices)
            .filter(id => formatType(devices, id) === "swap" && onSelectedDisks(id))
            .forEach(id => parts.push({ kind: "swap", text: `swap ${formatSize(deviceSize(devices, id))}` }));

    if (zram === true) {
        parts.push({ kind: "zram", text: "swap in memory (zram)" });
    }

    return { parts, text: parts.map(part => part.text).join(" · ") };
};

export const layoutLine = (args) => layoutParts(args).text;

/** Mount points that are planned to be (re)formatted, for the manual method's warning. */
export const reformattedMounts = (requests = []) => requests
        .filter(request => v(request.reformat) && v(request["mount-point"]))
        .map(request => v(request["mount-point"]))
        .sort(byMount);
