/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 2's confirmation line, computed from the chosen zone: "Your clock will
 * read 9:41 AM on Tuesday, August 18."
 */

export const clockReading = ({ locale = "en-US", now = new Date(), zone }) => {
    if (!zone) {
        return null;
    }
    try {
        const time = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit", timeZone: zone }).format(now);
        const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", timeZone: zone, weekday: "long" }).format(now);
        return { date, time };
    } catch (error) {
        return null;
    }
};

/** Anaconda's ISO8601 local date-time spec for SetSystemDateTime, e.g. "2026-09-14T09:41". */
export const dateTimeSpec = ({ date, time }) => (date && time ? `${date}T${time}` : null);

/** Parts of "now" in the zone, for pre-filling manual date and time fields. */
export const zoneParts = ({ now = new Date(), zone }) => {
    try {
        const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
            day: "2-digit", hour: "2-digit", hourCycle: "h23", minute: "2-digit", month: "2-digit", timeZone: zone, year: "numeric",
        }).formatToParts(now).map(part => [part.type, part.value]));
        return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
    } catch (error) {
        return { date: "", time: "" };
    }
};

/**
 * An offset-aware ISO 8601 string for a wall-clock date and time in a zone,
 * e.g. ("2026-08-18", "09:41", "America/Los_Angeles") -> "2026-08-18T09:41:00-07:00".
 */
export const isoInZone = ({ date, time, zone }) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "") || !/^\d{2}:\d{2}$/.test(time || "") || !zone) {
        return null;
    }
    const [year, month, day] = date.split("-").map(Number);
    const [hour, minute] = time.split(":").map(Number);
    if ([year, month, day, hour, minute].some(Number.isNaN)) {
        return null;
    }
    const wall = new Date(`${date}T${time}:00Z`);
    if (!year || !Number.isFinite(wall.getTime()) || wall.toISOString().slice(0, 16) !== `${date}T${time}`) return null;
    const asUtc = wall.getTime();
    const offsetAt = (instant) => {
        const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
            day: "numeric", hour: "numeric", hourCycle: "h23", minute: "numeric", month: "numeric", timeZone: zone, year: "numeric",
        }).formatToParts(new Date(instant)).map(part => [part.type, Number(part.value)]));
        const wall = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour % 24, parts.minute);
        return Math.round((wall - instant) / 60000);
    };
    // Two passes settle daylight-saving boundaries.
    let offset;
    try {
        offset = offsetAt(asUtc);
        offset = offsetAt(asUtc - offset * 60000);
    } catch (error) { return null; }
    const resolved = zoneParts({ now: new Date(asUtc - offset * 60000), zone });
    if (resolved.date !== date || resolved.time !== time) return null;
    const sign = offset >= 0 ? "+" : "-";
    const abs = Math.abs(offset);
    const pad = (n) => String(n).padStart(2, "0");
    return `${date}T${pad(hour)}:${pad(minute)}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
};

/** Commit a complete wall clock and verify Anaconda's actual clock readback. */
export const confirmManualClock = async ({ date, time, zone, setSystemDateTime, getSystemDateTime }) => {
    const spec = isoInZone({ date, time, zone });
    if (!spec) throw new Error("Choose a valid date (YYYY-MM-DD) and time (HH:MM).");
    await setSystemDateTime({ dateTimeSpec: spec });
    const reading = await getSystemDateTime();
    // Anaconda versions may return local ISO8601 without an explicit offset.
    const withOffset = typeof reading === "string" && /(?:Z|[+-]\d{2}:?\d{2})$/.test(reading);
    const actual = withOffset ? Date.parse(reading) : Date.parse(isoInZone({ date: typeof reading === "string" ? reading.slice(0, 10) : null, time: typeof reading === "string" ? reading.slice(11, 16) : null, zone }));
    if (!Number.isFinite(actual) || Math.abs(actual - Date.parse(spec)) >= 60000) {
        throw new Error("The clock did not confirm that date and time. Try again.");
    }
    return spec;
};
