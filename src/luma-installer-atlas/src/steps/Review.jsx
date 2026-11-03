/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 6 · Here is what will happen.
 * Every answer, each a link back to its step; the Layout line is generated
 * from the applied plan's device tree; the warning names the drive that
 * changes and every drive that does not. Nothing before the primary button
 * has written to a drive. The space check is anaconda-webui 68's
 * (ReviewConfiguration.jsx).
 */
import React, { useContext, useEffect, useMemo, useState } from "react";

import { getAppliedPartitioning } from "../apis/storage_partitioning.js";

import { getDevicesAction, getPartitioningDataAction, setAppliedPartitioningAction } from "../actions/storage-actions.js";

import { driveModel, driveTitle } from "../model/drives.js";
import { formatSize } from "../model/format.js";
import { findLocale, localeNativeName } from "../model/languages.js";
import { layoutLine } from "../model/layout.js";
import { durationClause, installingText, networkAdvice } from "../model/delivery.js";
import { batteryAdvice } from "../model/power.js";
import { METHOD_NAMES, primaryLabel, reviewWarning } from "../model/review.js";

import { LanguageContext, ProbeContext, StorageContext } from "../contexts/Common.jsx";

import {
    useFreeSpaceForSystem,
    useOriginalDevices,
    useOriginalExistingSystems,
    usePlannedActions,
    usePlannedDevices,
    usePlannedMountPoints,
    useRequiredSize,
} from "../hooks/Storage.jsx";

import { Actions, Button, Loading, Spacer, Step, Summary, Warn } from "../atlas/components.jsx";
import { probeBattery, probeNetwork } from "../system/host.js";

const useBattery = (initial) => {
    const [battery, setBattery] = useState(initial);
    useEffect(() => {
        const read = () => probeBattery().then(setBattery).catch(() => {});
        const timer = setInterval(read, 20000);
        read();
        return () => clearInterval(timer);
    }, []);
    return battery;
};

// Only an install that downloads needs the network; poll it while the step is
// open so plugging in a cable unblocks the button without a reload.
const useNetwork = (initial, media) => {
    const [network, setNetwork] = useState(initial);
    const downloads = media?.source === "online" || media?.source === "test";
    useEffect(() => {
        if (!downloads) {
            return;
        }
        const read = () => probeNetwork().then(setNetwork).catch(() => {});
        const timer = setInterval(read, 3000);
        read();
        return () => clearInterval(timer);
    }, [downloads]);
    return network;
};

export const ReviewStep = ({ answers, back, dispatch, go, index, next, panelRef }) => {
    const { language, languages } = useContext(LanguageContext);
    const probe = useContext(ProbeContext);
    const { appliedPartitioning, diskSelection, luks, storageScenarioId } = useContext(StorageContext);
    const originalDevices = useOriginalDevices();
    const existingSystems = useOriginalExistingSystems();
    const plannedDevices = usePlannedDevices();
    const plannedMountPoints = usePlannedMountPoints();
    const plannedActions = usePlannedActions();
    const freeSpace = useFreeSpaceForSystem();
    const requiredSize = useRequiredSize();
    const battery = useBattery(probe?.battery);
    const network = useNetwork(probe?.network, probe?.media);

    // The store learns of an applied plan from a change signal. After a
    // viewer reload there is no signal, so ask Anaconda for the plan it holds.
    useEffect(() => {
        if (appliedPartitioning) {
            return;
        }
        getAppliedPartitioning()
                .then(async path => {
                    if (path) {
                        await dispatch(getPartitioningDataAction({ partitioning: path }));
                        dispatch(setAppliedPartitioningAction({ appliedPartitioning: path }));
                        await dispatch(getDevicesAction());
                    }
                })
                .catch(() => {});
    }, [appliedPartitioning, dispatch]);


    const disk = diskSelection.selectedDisks[0];
    const payload = probe?.payload;
    const planReady = !!appliedPartitioning && Object.keys(plannedMountPoints || {}).length > 0;

    const layout = useMemo(() => layoutLine({
        devices: plannedDevices,
        mountPoints: plannedMountPoints,
        selectedDisks: diskSelection.selectedDisks,
        zram: payload?.zram ?? null,
    }), [diskSelection.selectedDisks, payload?.zram, plannedDevices, plannedMountPoints]);

    const facts = answers.driveFacts;
    const knownDevices = facts ? { ...originalDevices, ...facts.devices } : originalDevices;
    const knownSystems = facts?.existingSystems?.length ? facts.existingSystems : existingSystems;
    const warning = reviewWarning({
        actions: plannedActions,
        devices: knownDevices,
        existingSystems: knownSystems,
        installerDisks: probe?.installerDisks || [],
        reformatted: answers.reformatted || [],
        scenarioId: storageScenarioId,
        selectedDisks: diskSelection.selectedDisks,
        usableDisks: diskSelection.usableDisks,
    });

    const locale = findLocale(languages, language);
    const installing = installingText({ media: probe?.media, payload });

    const lockedValue = storageScenarioId === "mount-point-mapping" || storageScenarioId === "home-reuse"
        ? "Not changed"
        : luks.encrypted ? "Yes, with a passphrase" : "No";

    const rows = [
        installing && { key: "installing", term: "Installing", value: installing },
        { key: "language", onEdit: () => go(0), term: "Language", value: locale ? localeNativeName(locale) : language },
        disk && { key: "drive", onEdit: () => go(1), term: "Drive", value: `${driveTitle(knownDevices[disk])} · ${driveModel(knownDevices[disk])}` },
        storageScenarioId && { key: "method", onEdit: () => go(1), term: "Method", value: METHOD_NAMES[storageScenarioId] },
        { key: "locked", onEdit: () => go(2), term: "Locked", value: lockedValue },
        { key: "layout", onEdit: () => go(storageScenarioId === "mount-point-mapping" ? 2 : 1), term: "Layout", value: layout },
        storageScenarioId === "mount-point-mapping" && typeof answers.manualKeepBootOrder === "boolean" && { key: "startup", onEdit: () => go(2), term: "Startup", value: answers.manualKeepBootOrder ? "Keep the current default" : "Start Luma by default" },
    ].filter(Boolean);

    const power = batteryAdvice(battery, durationClause(probe?.media));
    const offline = networkAdvice({ media: probe?.media, network });
    const shortOfSpace = requiredSize !== undefined && freeSpace !== undefined && requiredSize > freeSpace;
    const blocked = !planReady || !!power?.block || shortOfSpace || !!offline?.block;

    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="quiet" onClick={back}>Back</Button>
                  <Spacer />
                  <Button variant="primary" disabled={blocked} onClick={next} data-first-control={undefined}>
                      {primaryLabel({ reformatted: answers.reformatted || [], scenarioId: storageScenarioId })}
                  </Button>
              </Actions>
          )}
          id="review"
          index={index}
          lede="Nothing has been changed yet. This is the last point where you can go back."
          panelRef={panelRef}
          title="Here is what will happen."
        >
            {!planReady && <Loading>Reading the plan</Loading>}
            {planReady && <Summary rows={rows} />}
            {planReady && warning.strong && <Warn strong={warning.strong} plain={warning.plain} />}
            {shortOfSpace && (
                <Warn
                  strong="There is not enough room."
                  plain={`Luma needs about ${formatSize(requiredSize)}, and this plan leaves ${formatSize(freeSpace)}.`}
                />
            )}
            {offline && <Warn live strong={offline.strong} plain={offline.plain} />}
            {power && <Warn live strong={power.strong} plain={power.plain} />}
        </Step>
    );
};
