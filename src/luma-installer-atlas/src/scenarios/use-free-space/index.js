/*
 * Copyright (C) 2025 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Derived from anaconda-webui 68. Luma change: presentation strings moved to
 * the Atlas destination step; this object keeps only backend facts.
 */

import { useAvailabilityUseFreeSpace } from "./UseFreeSpace.jsx";

/** Installs into free space and keeps what is already on the drive. */
export const scenario = {
    getAvailability: useAvailabilityUseFreeSpace,
    id: "use-free-space",
    // CLEAR_PARTITIONS_NONE = 0
    initializationMode: 0,
    method: "AUTOMATIC",
};
