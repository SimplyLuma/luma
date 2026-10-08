/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * The installer viewer can reload (a crash, a stray key). Anaconda keeps every
 * answer, but the page would forget where the person was and, on step 7,
 * which installation task it was following. This keeps only position,
 * non-secret choices, the task path and a sanitized failure message, in the
 * viewer's session storage. Task.Finish consumes its saved exception, so the
 * recorded failure also prevents a reload from treating it as successful.
 * Passphrases and passwords are never stored.
 */

const KEY = "luma-atlas-session";

const read = () => {
    try {
        return JSON.parse(window.sessionStorage.getItem(KEY)) || {};
    } catch (error) {
        return {};
    }
};

export const loadSession = () => read();

export const saveSession = (patch) => {
    try {
        window.sessionStorage.setItem(KEY, JSON.stringify({ ...read(), ...patch }));
    } catch (error) {
        // Storage can be unavailable; the installer still works without it.
    }
};
