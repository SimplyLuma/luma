/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Tells atlas-viewer (libexec/atlas-viewer) that Atlas's usable page or recovery
 * screen is on screen. Application starts this only after backend readiness;
 * the boot artwork must not release Plymouth for an intermediate placeholder.
 * Every few
 * seconds, from the second of two animation frames (so after a paint), the
 * page rewrites /run/luma-atlas/viewer-heartbeat. A viewer that produces no
 * frames runs no animation callbacks, so no heartbeat arrives: atlas-viewer
 * then starts the viewer again in safe mode and, failing that, shows its own
 * error screen instead of a silent grey window.
 */
import cockpit from "cockpit";

export const VIEWER_HEARTBEAT = "/run/luma-atlas/viewer-heartbeat";
const INTERVAL_MS = 3000;

export const startViewerHeartbeat = () => {
    if (typeof cockpit.file !== "function" || typeof window.requestAnimationFrame !== "function") {
        return () => {};
    }

    let file = null;
    let pending = false;
    let stopped = false;

    const write = () => {
        if (!file) {
            file = cockpit.file(VIEWER_HEARTBEAT, { superuser: "try" });
        }
        return file.replace(`${Date.now()}\n`);
    };

    const beat = () => {
        if (stopped || pending) {
            return;
        }
        pending = true;
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
            Promise.resolve()
                    .then(write)
                    .catch(() => {})
                    .finally(() => { pending = false });
        }));
    };

    beat();
    const timer = window.setInterval(beat, INTERVAL_MS);
    return () => {
        stopped = true;
        window.clearInterval(timer);
        file?.close?.();
    };
};
