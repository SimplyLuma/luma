/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Plain-language formatting shared by every Atlas step. No cockpit imports:
 * this module is unit-tested directly under node.
 */

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

/**
 * Format a byte count the way drives are sold: decimal units, and no more
 * precision than a person needs ("1 TB", "500 GB", "600 MB", "1.5 GB").
 */
export const formatSize = (bytes) => {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) {
        return "0 B";
    }

    let unit = 0;
    let scaled = value;
    while (scaled >= 1000 && unit < UNITS.length - 1) {
        scaled /= 1000;
        unit += 1;
    }

    // Values that would round up to the next unit read better in it.
    if (Math.round(scaled) >= 1000 && unit < UNITS.length - 1) {
        scaled /= 1000;
        unit += 1;
    }

    let text;
    if (scaled >= 10) {
        text = String(Math.round(scaled));
    } else {
        text = (Math.round(scaled * 10) / 10).toString();
    }

    return `${text} ${UNITS[unit]}`;
};

const IEC = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];

/**
 * Partition sizes are planned in binary units ("2 GiB", "600 MiB"); say them
 * the same way instead of rounding them into odd decimal figures.
 */
export const formatBinarySize = (bytes) => {
    let scaled = Number(bytes);
    if (!Number.isFinite(scaled) || scaled <= 0) {
        return "0 B";
    }
    let unit = 0;
    while (scaled >= 1024 && unit < IEC.length - 1) {
        scaled /= 1024;
        unit += 1;
    }
    const text = scaled >= 10 ? String(Math.round(scaled)) : (Math.round(scaled * 10) / 10).toString();
    return `${text} ${IEC[unit]}`;
};

/** "A", "A and B", "A, B and C". */
export const joinList = (items, conjunction = "and") => {
    const list = items.filter(item => item !== undefined && item !== null && item !== "");
    if (list.length === 0) {
        return "";
    }
    if (list.length === 1) {
        return list[0];
    }
    return `${list.slice(0, -1).join(", ")} ${conjunction} ${list[list.length - 1]}`;
};

/** tz database names use underscores: "Los_Angeles" -> "Los Angeles", "Argentina/Buenos_Aires" -> "Buenos Aires, Argentina". */
export const displayCity = (city) => {
    if (!city) {
        return "";
    }
    const parts = city.split("/").map(part => part.replace(/_/g, " "));
    if (parts.length === 1) {
        return parts[0];
    }
    return `${parts[parts.length - 1]}, ${parts.slice(0, -1).join(", ")}`;
};

/* The tz database calls the continent "America"; people call it the Americas. */
const REGION_NAMES = {
    America: "Americas",
    Etc: "Other",
};

export const displayRegion = (region) => REGION_NAMES[region] || (region || "").replace(/_/g, " ");

/** Replace straight quotes and apostrophes with typographic ones. */
export const curly = (text) => String(text)
        .replace(/(^|[\s(“])'/g, "$1‘")
        .replace(/'/g, "’")
        .replace(/(^|[\s(‘])"/g, "$1“")
        .replace(/"/g, "”");
