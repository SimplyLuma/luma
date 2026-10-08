/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * The few things Atlas does outside Anaconda's D-Bus modules, each one a
 * narrow call into the installer runtime:
 *   - read-only facts (atlas-probe)
 *   - a suspend and lid-switch inhibitor held for the whole of step 7
 *   - ejecting the installer medium before step 8 (atlas-media eject)
 *   - saving the logs after a failure (atlas-media save-logs)
 *   - recording "sign me in automatically" for the Luma kickstart %post
 * None of these touch a target drive before the review step.
 */

import cockpit from "cockpit";

import { error as logError } from "../helpers/log.js";

export const LIBEXEC = "/usr/libexec/luma-installer-atlas";
export const AUTOLOGIN_INTENT = "/run/luma-atlas/autologin";

const spawnJson = async (args) => {
    let output;
    try {
        output = await cockpit.spawn(args, { err: "message", superuser: "try" });
    } catch (exception) {
        // atlas-media exits non-zero with a JSON reason on stdout.
        output = exception?.message && exception.message.trim().startsWith("{") ? exception.message : null;
        if (!output) {
            throw exception;
        }
    }
    return JSON.parse(output);
};

export const probeSystem = () => spawnJson([`${LIBEXEC}/atlas-probe`, "system"]);
export const probeBattery = () => spawnJson([`${LIBEXEC}/atlas-probe`, "battery"]).then(result => result.battery);
export const probeNetwork = () => spawnJson([`${LIBEXEC}/atlas-probe`, "network"]);
export const probePayload = ({ ref, url }) => spawnJson([`${LIBEXEC}/atlas-probe`, "payload", url, ref]);

/**
 * Hold a logind inhibitor for sleep, idle and the lid switch until release()
 * is called. Resolves { held: boolean, release }. When the inhibitor cannot
 * be taken, "You can close the lid" is not said (handoff §3, step 7).
 */
export const holdSuspendInhibitor = () => new Promise(resolve => {
    let settled = false;
    const process = cockpit.spawn([
        "systemd-inhibit",
        "--what=sleep:idle:handle-lid-switch:handle-suspend-key",
        "--who=Luma installer",
        "--why=Luma is being installed",
        "--mode=block",
        "sleep", "infinity",
    ], { err: "message", superuser: "try" });

    const release = () => {
        try {
            process.close("terminated");
        } catch (exception) {
            logError("atlas: releasing the suspend inhibitor failed", exception?.message);
        }
    };

    process.catch(exception => {
        if (!settled) {
            settled = true;
            logError("atlas: suspend inhibitor not held", exception?.message);
            resolve({ held: false, release: () => {} });
        }
    });

    // systemd-inhibit either fails at once or blocks. Confirm the lock is
    // listed before promising anything.
    setTimeout(async () => {
        if (settled) {
            return;
        }
        try {
            const list = await cockpit.spawn(["systemd-inhibit", "--list", "--no-pager", "--no-legend"], { err: "ignore", superuser: "try" });
            settled = true;
            const held = list.includes("Luma installer") && list.includes("handle-lid-switch");
            resolve({ held, release });
        } catch (exception) {
            settled = true;
            resolve({ held: false, release });
        }
    }, 700);
});

export const ejectMedium = () => spawnJson([`${LIBEXEC}/atlas-media`, "eject"]);

export const saveLogs = (targetDisks = []) => spawnJson([`${LIBEXEC}/atlas-media`, "save-logs", ...targetDisks]);

export const FIRST_BOOT_APPS_INTENT = "/run/luma-atlas/first-boot-apps.json";

/** The "Get more apps" choice, for the kickstart to copy into /etc/luma. */
export const setFirstBootAppsIntent = async (choice) => {
    await cockpit.spawn(["mkdir", "-p", "/run/luma-atlas"], { superuser: "try" });
    const file = cockpit.file(FIRST_BOOT_APPS_INTENT, { superuser: "try" });
    try {
        await file.replace(`${JSON.stringify(choice)}\n`);
    } finally {
        file.close();
    }
};

/** Written to the installer's own /run (memory), never to a drive. */
export const setAutologinIntent = async (username) => {
    const file = cockpit.file(AUTOLOGIN_INTENT, { superuser: "try" });
    try {
        if (username) {
            await cockpit.spawn(["mkdir", "-p", "/run/luma-atlas"], { superuser: "try" });
            await file.replace(`${username}\n`);
        } else {
            await file.replace(null);
        }
    } finally {
        file.close();
    }
};

export const recordLocationIntent = timezone => spawnJson([`${LIBEXEC}/atlas-media`, "record-location", timezone]);
