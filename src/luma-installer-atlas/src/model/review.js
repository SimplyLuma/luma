/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 6 sentences. The warning names the drive that will change and every
 * drive that will not be touched, because the second half is what people are
 * afraid of. The primary button is the only place "erase" is a button.
 */

import { descendants, driveModel, driveTotal, systemsOnDrive } from "./drives.js";
import { formatSize, joinList } from "./format.js";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

const capitalise = (text) => text ? text[0].toUpperCase() + text.slice(1) : text;

/**
 * "the 1 TB drive", "the 500 GB drive with Windows 11 on it",
 * "your installer USB drive". When two drives would read the same, the model
 * name is added so the sentence stays unambiguous.
 */
export const describeDrive = ({ devices, disk, existingSystems, installerDisks = [], others = [] }) => {
    if (installerDisks.includes(disk)) {
        return "your installer USB drive";
    }
    const size = formatSize(driveTotal(devices[disk]));
    const clash = others.some(other => other !== disk && !installerDisks.includes(other) &&
        formatSize(driveTotal(devices[other])) === size);
    const model = clash ? ` ${driveModel(devices[disk])}` : "";
    const systems = [...new Set(systemsOnDrive(devices, existingSystems, disk).map(system => v(system["os-name"])))];
    const withSystems = systems.length ? ` with ${joinList(systems)} on it` : "";
    return `the ${size}${model} drive${withSystems}`;
};

const destroyedOn = ({ actions, devices, disk }) => {
    const onDisk = new Set([disk, ...descendants(devices, disk)]);
    return (actions || []).filter(action => v(action["action-type"]) === "destroy" && onDisk.has(v(action["device-id"])));
};

/**
 * @returns {{ strong: string, plain: string }}
 */
export const reviewWarning = ({
    actions = [],
    devices = {},
    existingSystems = [],
    installerDisks = [],
    reformatted = [],
    scenarioId,
    selectedDisks = [],
    usableDisks = [],
}) => {
    const disk = selectedDisks[0];
    const allDisks = [...new Set([...usableDisks, ...installerDisks])].filter(d => devices[d]);
    const size = disk ? formatSize(driveTotal(devices[disk])) : "";

    const untouched = allDisks.filter(d => !selectedDisks.includes(d));
    const untouchedDescriptions = untouched
            .sort((a, b) => Number(installerDisks.includes(a)) - Number(installerDisks.includes(b)))
            .map(d => describeDrive({ devices, disk: d, existingSystems, installerDisks, others: allDisks }));
    const untouchedSentence = untouchedDescriptions.length
        ? `${capitalise(joinList(untouchedDescriptions))} will not be touched.`
        : "";

    const selectedSystems = disk
        ? [...new Set(systemsOnDrive(devices, existingSystems, disk).map(system => v(system["os-name"])))]
        : [];

    let strong;
    let lead = "";
    switch (scenarioId) {
    case "erase-all":
        strong = selectedSystems.length
            ? `Everything on the ${size} drive, including ${joinList(selectedSystems)}, will be erased.`
            : `Everything on the ${size} drive will be erased.`;
        break;
    case "use-free-space": {
        const destroyed = destroyedOn({ actions, devices, disk });
        strong = destroyed.length
            ? `Some partitions on the ${size} drive will be erased.`
            : `Luma will use the free space on the ${size} drive.`;
        lead = destroyed.length ? "" : "Nothing already on it will be erased.";
        break;
    }
    case "home-reuse":
        strong = `The system on the ${size} drive will be replaced.`;
        lead = "Your home folder is kept.";
        break;
    case "mount-point-mapping":
        strong = reformatted.length
            ? `${capitalise(joinList(reformatted))} will be formatted, and what is on ${reformatted.length > 1 ? "them" : "it"} erased.`
            : "No partitions will be formatted.";
        break;
    default:
        strong = "";
    }

    return { plain: [lead, untouchedSentence].filter(Boolean).join(" "), strong };
};

export const primaryLabel = ({ reformatted = [], scenarioId }) => {
    switch (scenarioId) {
    case "erase-all":
        return "Erase this drive and install";
    case "home-reuse":
        return "Replace the system and install";
    case "mount-point-mapping":
        return reformatted.length ? "Format and install" : "Install Luma";
    default:
        return "Install Luma";
    }
};

export const METHOD_NAMES = {
    "erase-all": "Use the whole drive",
    "home-reuse": "Reinstall Luma",
    "mount-point-mapping": "I will choose the partitions myself",
    "use-free-space": "Share with what is already there",
};
