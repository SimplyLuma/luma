/*
 * Copyright (C) 2023 Red Hat, Inc.
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Derived from anaconda-webui 68 src/components/storage/scenarios/index.js.
 * Luma change: the Cockpit storage editor scenario ("use configured storage")
 * is not offered, because Atlas does not ship cockpit-storaged.
 */
import { useEffect, useState } from "react";

import { scenario as scenarioEraseAll } from "./erase-all/index.js";
import { scenario as scenarioMountPointMapping } from "./mount-point-mapping/index.js";
import { scenario as scenarioReinstall } from "./reinstall/index.js";
import { scenario as scenarioUseFreeSpace } from "./use-free-space/index.js";

export const useScenariosAvailability = () => {
    const reinstall = scenarioReinstall.getAvailability();
    const useFreeSpace = scenarioUseFreeSpace.getAvailability();
    const eraseAll = scenarioEraseAll.getAvailability();
    const mountPointMapping = scenarioMountPointMapping.getAvailability();
    const [scenarioAvailability, setScenarioAvailability] = useState();

    useEffect(() => {
        setScenarioAvailability({
            [scenarioEraseAll.id]: eraseAll,
            [scenarioMountPointMapping.id]: mountPointMapping,
            [scenarioReinstall.id]: reinstall,
            [scenarioUseFreeSpace.id]: useFreeSpace,
        });
    }, [eraseAll, mountPointMapping, reinstall, useFreeSpace]);

    if (Object.values(scenarioAvailability || {}).some(value => value === undefined)) {
        return undefined;
    }

    return scenarioAvailability;
};

/* Atlas presents these in the design's order: whole drive, share, reinstall, by hand. */
export const scenarios = [
    scenarioEraseAll,
    scenarioUseFreeSpace,
    scenarioReinstall,
    scenarioMountPointMapping,
];
