/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 7: Anaconda task progress -> the four Atlas phases.
 *
 * Anaconda 44.30 (pyanaconda/modules/boss/installation.py) runs one queue:
 *   installation: ENVIRONMENT, STORAGE, ENVIRONMENT (pre scripts),
 *                 SOFTWARE (pre-install, payload), STORAGE (late),
 *                 BOOTLOADER (install), SYSTEM (post-install), [STORAGE snapshot]
 *   configuration: SYSTEM (certificates, OS, network, users, add-ons),
 *                 BOOTLOADER (initramfs), security, storage,
 *                 SYSTEM (write configs, %post scripts)
 *
 * Phases light in turn and never go backwards:
 *   0 Preparing the drive          ENVIRONMENT, STORAGE
 *   1 Installing Luma              SOFTWARE
 *   2 Setting up your system       BOOTLOADER install and SYSTEM configuration
 *   3 Finishing up                BOOTLOADER again (initramfs) once SYSTEM has run,
 *                                  through the final configs and scripts
 */

export const PHASES = [
    "Preparing the drive",
    "Installing Luma",
    "Setting up your system",
    "Finishing up",
];

export const initialProgress = () => ({
    best: 0,
    category: null,
    done: false,
    failed: false,
    message: "",
    phase: 0,
    payloadMeasured: false,
    seenSystem: false,
    step: 0,
    steps: 0,
    subFraction: 0,
});

export const applyCategory = (state, category) => {
    let phase = state.phase;
    let seenSystem = state.seenSystem;

    switch (category) {
    case "ENVIRONMENT_CONFIGURATION":
    case "STORAGE_CONFIGURATION":
        phase = Math.max(phase, 0);
        break;
    case "SOFTWARE_INSTALLATION":
        phase = Math.max(phase, 1);
        break;
    case "BOOTLOADER_INSTALLATION":
        phase = Math.max(phase, seenSystem ? 3 : 2);
        break;
    case "SYSTEM_CONFIGURATION":
        phase = Math.max(phase, 2);
        seenSystem = true;
        break;
    default:
        break;
    }

    return { ...state, category, phase, seenSystem, subFraction: 0 };
};

/* The payload task reports its own percentage ("Receiving objects: 45% ...")
 * while the step counter stands still; use it to keep the meter moving. */
const PERCENT = /(\d{1,3}(?:\.\d+)?)\s*%/;

export const applyProgress = (state, step, message) => {
    const next = { ...state, message: message || state.message };
    if (typeof step === "number" && step >= state.step) {
        if (step > state.step) {
            next.subFraction = 0;
        }
        next.step = step;
    }
    const match = message ? PERCENT.exec(message) : null;
    if (state.category === "SOFTWARE_INSTALLATION") {
        // A local OSTree pull reports "Writing objects" without a count.
        // Missing measurement is activity, not a frozen invented percentage.
        next.payloadMeasured = !!match;
    }
    if (match && state.category === "SOFTWARE_INSTALLATION") {
        next.subFraction = Math.min(0.99, Math.max(state.subFraction, Number(match[1]) / 100));
    }
    return next;
};

export const applySteps = (state, steps) => ({ ...state, steps: Number(steps) || state.steps });

export const applySucceeded = (state) => ({ ...state, done: true, phase: PHASES.length, subFraction: 0 });

export const applyFailed = (state) => ({ ...state, failed: true });

/*
 * Where installs spend their time. Anaconda counts 49 tasks and the payload is
 * one of them, but in a VM install of an OSTree payload it took 380 of 388
 * seconds. Each band is filled only by what Anaconda reports: task steps, and
 * the payload's own "Receiving objects: N%" messages.
 */
const BANDS = [
    { end: 5, start: 0 },
    { end: 90, start: 5 },
    { end: 96, start: 90 },
    { end: 99, start: 96 },
];

/** Whole-number percentage. Never shows 100 until Anaconda says it succeeded. */
export const percent = (state) => {
    if (state.done) {
        return 100;
    }
    if (state.phase === 1 && state.category === "SOFTWARE_INSTALLATION" && !state.payloadMeasured) {
        return null;
    }
    const band = BANDS[Math.min(state.phase, BANDS.length - 1)];
    let within;
    if (state.phase === 1) {
        within = state.subFraction;
    } else {
        within = state.steps ? Math.min(1, state.step / state.steps) : 0;
    }
    const value = band.start + (band.end - band.start) * within;
    return Math.max(state.best || 0, Math.min(99, Math.floor(value)));
};

/** Keep the meter from moving backwards between reports. */
export const remember = (state) => ({ ...state, best: percent(state) ?? state.best });

/** "done" | "doing" | "todo" for each phase. */
export const phaseStates = (state) => PHASES.map((_, index) => {
    if (state.done || index < state.phase) {
        return "done";
    }
    return index === state.phase ? (state.failed ? "failed" : "doing") : "todo";
});

/** §7 failure copy, in the words of the phase that failed. */
export const failureSentence = (state) => {
    const where = [
        "while preparing the drive",
        "while copying",
        "while setting things up",
        "while finishing off",
    ][Math.min(state.phase, 3)];
    return {
        plain: "Nothing on your other drives was touched.",
        strong: `Something went wrong ${where}.`,
    };
};
