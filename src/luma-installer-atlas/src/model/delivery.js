/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Where Luma comes from and where its updates will come from (ADR-030 §9).
 * The installer kickstart's %pre decides the source at boot: the payload on
 * the USB drive when the drive carries the channel's release, otherwise
 * Luma's HTTPS repository. atlas-probe reports that choice as `media`
 * ({channel, source: "medium" | "online" | "test", ref}) and NetworkManager's
 * connectivity as `network`.
 */

export const CHANNEL_NAMES = { beta: "Beta", nightly: "Nightly", stable: "Stable" };

const downloads = (media) => media?.source === "online" || media?.source === "test";

/**
 * The release being installed, exactly as its payload's os-release
 * PRETTY_NAME says ("Luma (Prairie, Beta 0, Nightly 20260916)"). atlas-probe
 * reads it from the medium's OSTree commit; an online install has not
 * downloaded it yet, and a payload that is not Luma is not named, so both
 * give null and callers say plain "Luma".
 */
export const releaseName = (payload) => {
    const name = (payload?.prettyName || "").trim();
    return /^Luma(\s|$)/.test(name) ? name : null;
};

/** The Review step's "Installing" value. */
export const installingText = ({ media, payload }) => {
    const name = payload?.prettyName || payload?.osName || null;
    if (!media) {
        return name || payload?.ref || null;
    }
    const channel = CHANNEL_NAMES[media.channel];
    if (media.source === "test") {
        return `${name || payload?.ref || media.ref} (a test system) · updates from ${channel}`;
    }
    const source = media.source === "medium" ? "from this USB drive" : "downloaded as it installs";
    const release = releaseName(payload);
    // "Luma (Prairie, Beta 0, Nightly 20260916)" already names its channel.
    const named = release && channel && new RegExp(`\\b${channel}\\b`, "i").test(release);
    return [release || "Luma", named ? null : channel, source].filter(Boolean).join(" · ");
};

/** The completed install has one instruction and its restart action. */
export const finishedLede = () => "Take the USB drive out, then restart.";

/**
 * An online install cannot start without the internet. "unknown" (no
 * NetworkManager verdict, but a default route) is allowed through; Anaconda's
 * own pull reports a real failure.
 */
export const networkAdvice = ({ media, network }) => {
    if (!downloads(media) || !network) {
        return null;
    }
    if (network.connectivity === "full" || network.connectivity === "unknown") {
        return null;
    }
    return {
        block: true,
        plain: network.connectivity === "portal"
            ? "Luma downloads itself while it installs, and this network wants you to sign in to it first. Use a network that does not, such as a cable to your router."
            : "Luma downloads itself while it installs, and this computer is not connected to the internet. Plug in a network cable to carry on.",
        strong: "Connect to the internet first.",
    };
};

/** Step 7's duration promise (handoff §3: an estimate, or "usually under ten minutes"). */
export const durationSentence = (media) => downloads(media)
    ? "How long this takes depends on your internet connection."
    : "This usually takes under ten minutes.";

/** The same promise inside the low-battery warning. */
export const durationClause = (media) => downloads(media)
    ? "and downloading Luma can take a while"
    : "and this usually takes under ten minutes";

