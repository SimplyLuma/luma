/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Steps 4 and 5 field rules, with messages written for the field they sit
 * under. Nothing here opens a dialog: every problem is a sentence beneath the
 * field it concerns.
 */

/* Names the base system keeps for itself (anaconda-webui 68's list). The
 * probe adds every account defined in the payload's own /usr/lib/passwd. */
export const RESERVED_USERNAMES = [
    "root", "bin", "daemon", "adm", "lp", "sync", "shutdown", "halt", "mail",
    "operator", "games", "ftp", "nobody", "home", "system",
];

const q = (text) => `“${text}”`;

/** @returns {string|null} a message, or null when the username is acceptable */
export const usernameProblem = (username, { taken = [] } = {}) => {
    const name = username || "";
    if (name.length === 0) {
        return null;
    }
    if (/\s/.test(name)) {
        return "Usernames cannot have spaces.";
    }
    if (/[A-Z]/.test(name)) {
        return "Use lowercase letters only.";
    }
    if (!/^[a-z_]/.test(name)) {
        return "Start with a lowercase letter.";
    }
    if (!/^[a-z_][a-z0-9_-]*$/.test(name)) {
        return "Use only lowercase letters, numbers, dashes and underscores.";
    }
    if (name.length > 32) {
        return "Keep it to 32 characters or fewer.";
    }
    if (name === "root") {
        return `${q("root")} is reserved for the system. Pick another name.`;
    }
    if (RESERVED_USERNAMES.includes(name) || taken.includes(name)) {
        return `${q(name)} is already used by the system. Pick another name.`;
    }
    return null;
};

export const fullNameProblem = (fullName) => (fullName || "").includes(":")
    ? "Names cannot contain a colon."
    : null;

/** A local fallback when Anaconda's GuessUsernameFromFullName is unavailable. */
export const guessUsername = (fullName) => {
    const first = (fullName || "").trim().split(/\s+/)[0] || "";
    return first
            .normalize("NFKD")
            .replace(/[̀-ͯ]/g, "")
            .toLowerCase()
            .replace(/[^a-z0-9_-]/g, "")
            .replace(/^[^a-z_]+/, "")
            .slice(0, 32);
};

const policyValue = (policy, key, fallback) => {
    const value = policy?.[key];
    const raw = value && typeof value === "object" && "v" in value ? value.v : value;
    return raw === undefined ? fallback : raw;
};

/**
 * Validate a secret and its confirmation.
 * @param {"password"|"passphrase"} noun
 * @returns {{ valid: boolean, secretProblem: string|null, confirmProblem: string|null, notes: string[] }}
 */
export const secretProblems = ({ confirm, noun = "password", policy, quality = null, secret }) => {
    const minLength = Number(policyValue(policy, "min-length", 6));
    const minQuality = Number(policyValue(policy, "min-quality", 1));
    const strict = Boolean(policyValue(policy, "is-strict", false));
    const value = secret || "";
    const again = confirm || "";
    const notes = [];

    let secretProblem = null;
    if (value.length > 0 && value.length < minLength) {
        secretProblem = `Use at least ${minLength} characters.`;
    } else if (value.length > 0 && strict && quality !== null && quality < minQuality) {
        secretProblem = `This ${noun} is too easy to guess.`;
    }

    if (!secretProblem && value.length > 0 && !strict && quality !== null && quality < Math.max(minQuality, 50)) {
        notes.push(`This ${noun} is easy to guess.`);
    }

    if (noun === "passphrase" && value.length > 0 && !/^[\x20-\x7E]*$/.test(value)) {
        notes.push("Some of these characters may be hard to type when the computer starts.");
    }

    let confirmProblem = null;
    if (again.length > 0 && value !== again) {
        confirmProblem = "These do not match.";
    }

    const valid = value.length >= minLength && value === again && !secretProblem;
    return { confirmProblem, notes, secretProblem, valid };
};
