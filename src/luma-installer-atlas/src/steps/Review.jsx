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

import { _getProperty } from "../apis/helpers.js";
import { getAppliedPartitioning } from "../apis/storage_partitioning.js";
import { getNTPEnabled } from "../apis/timezone.js";
import { UsersClient } from "../apis/users.js";

import { getDevicesAction, getPartitioningDataAction, setAppliedPartitioningAction } from "../actions/storage-actions.js";

import { displayCity } from "../model/format.js";
import { driveModel, driveTitle } from "../model/drives.js";
import { formatSize } from "../model/format.js";
import { findLocale, localeNativeName } from "../model/languages.js";
import { layoutLine } from "../model/layout.js";
import { durationClause, installingText, networkAdvice } from "../model/delivery.js";
import { batteryAdvice } from "../model/power.js";
import catalog from "../data/app-collections.json";
import { reviewAppsText } from "../model/apps.js";
import { METHOD_NAMES, primaryLabel, reviewWarning } from "../model/review.js";

import { LanguageContext, ProbeContext, StorageContext, TimezoneContext, UsersContext } from "../contexts/Common.jsx";

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
    const { timezone } = useContext(TimezoneContext);
    const accounts = useContext(UsersContext);
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
    const [ntp, setNtp] = useState(null);

    useEffect(() => {
        getNTPEnabled().then(value => setNtp(!!value)).catch(() => setNtp(null));
    }, []);

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

    // The account as Anaconda holds it, so the row stays true after a reload.
    const [backendUser, setBackendUser] = useState(null);
    useEffect(() => {
        _getProperty(UsersClient, "/org/fedoraproject/Anaconda/Modules/Users", "org.fedoraproject.Anaconda.Modules.Users", "Users")
                .then(users => setBackendUser(users?.[0] ? { fullName: users[0].gecos?.v || "", userName: users[0].name?.v || "" } : null))
                .catch(() => setBackendUser(null));
    }, []);
    const account = backendUser?.userName ? backendUser : accounts;

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
    const [zoneRegion, ...zoneCity] = (timezone || "").split("/");
    const installing = installingText({ media: probe?.media, payload });

    const lockedValue = storageScenarioId === "mount-point-mapping" || storageScenarioId === "home-reuse"
        ? "Not changed"
        : luks.encrypted ? "Yes, with a passphrase" : "No";

    const rows = [
        installing && { key: "installing", term: "Installing", value: installing },
        { key: "language", onEdit: () => go(0), term: "Language", value: locale ? localeNativeName(locale) : language },
        { key: "time", onEdit: () => go(1), term: "Time", value: `${displayCity(zoneCity.join("/") || zoneRegion)}${ntp === null ? "" : ntp ? " · set automatically" : " · set by hand"}` },
        disk && { key: "drive", onEdit: () => go(2), term: "Drive", value: `${driveTitle(knownDevices[disk])} · ${driveModel(knownDevices[disk])}` },
        storageScenarioId && { key: "method", onEdit: () => go(2), term: "Method", value: METHOD_NAMES[storageScenarioId] },
        { key: "locked", onEdit: () => go(3), term: "Locked", value: lockedValue },
        { key: "layout", onEdit: () => go(storageScenarioId === "mount-point-mapping" ? 3 : 2), term: "Layout", value: layout },
        { key: "apps", onEdit: () => go(5), term: "Apps", value: reviewAppsText(catalog, answers.collections || []) },
        { key: "account", onEdit: () => go(4), term: "Account", value: `${account.fullName ? `${account.fullName} · ` : ""}${account.userName}${answers.autologin ? " · signs in automatically" : ""}` },
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
