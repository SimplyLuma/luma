/* SPDX-License-Identifier: LGPL-2.1-or-later */

/* Explicit choices win; a fresh installer without a preference starts dark. */
export const installerAppearance = ({ override, bootOptions, contrast = false, light = false } = {}) => {
    if (override === "contrast") return { contrast: "more", surface: "slate" };
    if (["paper", "ink", "slate"].includes(override)) return { contrast: null, surface: override };
    if (bootOptions?.surface) {
        return { contrast: bootOptions.contrast ? "more" : null, surface: bootOptions.surface };
    }
    if (contrast) return { contrast: "more", surface: "slate" };
    return { contrast: null, surface: light ? "paper" : "ink" };
};
