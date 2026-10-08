/*
 * Copyright (C) 2025 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Derived from anaconda-webui 68. Luma change: presentation strings moved to
 * the Atlas destination step; this object keeps only backend facts.
 */

import { useAvailabilityEraseAll } from "./EraseAll.jsx";

/** Completely erases the selected drive and creates Luma's default layout. */
export const scenario = {
    getAvailability: useAvailabilityEraseAll,
    id: "erase-all",
    // CLEAR_PARTITIONS_ALL = 1
    initializationMode: 1,
    method: "AUTOMATIC",
};
