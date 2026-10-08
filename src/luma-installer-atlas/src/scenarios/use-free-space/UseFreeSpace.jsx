/*
 * Copyright (C) 2024 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */

import cockpit from "cockpit";

import { useContext, useEffect, useState } from "react";

import { AvailabilityState } from "../helpers.js";

import {
    StorageContext,
} from "../../contexts/Common.jsx";

import {
    useDiskFreeSpace,
    useDiskTotalSpace,
    useRequiredSize,
} from "../../hooks/Storage.jsx";

const _ = cockpit.gettext;

export const useAvailabilityUseFreeSpace = (args) => {
    // Luma: Atlas has no designed reclaim-space (shrink) tool yet, so the default
    // is to report the shortfall instead of enforcing a reclaim dialog.
    const allowReclaim = args?.allowReclaim ?? false;
    const [scenarioAvailability, setScenarioAvailability] = useState();

    const { diskSelection } = useContext(StorageContext);
    const selectedDisks = diskSelection.selectedDisks;
    const diskFreeSpace = useDiskFreeSpace();
    const diskTotalSpace = useDiskTotalSpace();
    const requiredSize = useRequiredSize();

    useEffect(() => {
        if ([diskFreeSpace, diskTotalSpace, requiredSize].some((value) => value === undefined)) {
            return;
        }

        const availability = new AvailabilityState();

        availability.hidden = false;
        availability.available = !!selectedDisks.length;
        // Luma: keep the numbers so Atlas can say how much is free and needed.
        availability.freeSpace = diskFreeSpace;
        availability.requiredSize = requiredSize;

        if (diskFreeSpace > 0 && diskTotalSpace > 0) {
            availability.hidden = diskFreeSpace === diskTotalSpace;
        }
        if (diskFreeSpace < requiredSize) {
            availability.reason = _("Not enough free space on the selected disks.");
            availability.hint = cockpit.format(
                _("To use this option, resize or remove existing partitions to free up at least $0."),
                cockpit.format_bytes(requiredSize)
            );
            if (allowReclaim) {
                availability.enforceAction = true;
            } else {
                availability.available = false;
            }
        }
        setScenarioAvailability(availability);
    }, [allowReclaim, diskFreeSpace, diskTotalSpace, requiredSize, selectedDisks]);

    return scenarioAvailability;
};
