/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 3 · Where should Luma live?
 * Drive facts come from Anaconda's Storage module (blivet/udev) and its
 * existing-system scan. Disk selection, the scenario availability checks and
 * partitioning creation are anaconda-webui 68's (InstallationDestination.jsx,
 * scenarios/, hooks/Storage.jsx getNewPartitioning). Nothing here writes to a
 * drive: creating a partitioning only builds a plan in memory.
 */
import React, { useContext, useEffect, useMemo, useState } from "react";

import { runStorageTask, scanDevicesWithTask } from "../apis/storage.js";
import { setSelectedDisks } from "../apis/storage_disks_selection.js";
import { getAppliedPartitioning, resetPartitioning } from "../apis/storage_partitioning.js";

import { getDevicesAction, getDiskSelectionAction, setStorageScenarioAction } from "../actions/storage-actions.js";

import { error as logError } from "../helpers/log.js";
import { driveListProblem, driveRows, mergeDriveFacts } from "../model/drives.js";
import { formatSize, joinList } from "../model/format.js";

import { ProbeContext, StorageContext } from "../contexts/Common.jsx";

import {
    getNewPartitioning,
    useHomeReuseOptions,
    useOriginalDevices,
    useOriginalExistingSystems,
    useRequiredSize,
} from "../hooks/Storage.jsx";

import {
    Actions, Button, DriveGlyph, Group, Loading, MoreButton, Option, Options, Spacer, Step, Warn,
} from "../atlas/components.jsx";
import { scenarios, useScenariosAvailability } from "../scenarios/index.js";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

const methodCopy = ({ availability, bitlocker, id, systems }) => {
    const reason = (text) => ({ disabled: true, subtitle: text });
    switch (id) {
    case "erase-all":
        if (availability && !availability.available && availability.requiredSize) {
            return reason(`This drive is too small. Luma needs about ${formatSize(availability.requiredSize)}.`);
        }
        return { subtitle: "Erases everything on it and gives all the space to Luma", title: "Use the whole drive" };
    case "use-free-space":
        if (bitlocker) {
            return reason("Windows has locked this drive; turn BitLocker off in Windows first");
        }
        if (availability && !availability.available && availability.requiredSize) {
            return reason(`Not enough free space. Luma needs about ${formatSize(availability.requiredSize)}; ${formatSize(availability.freeSpace)} is free.`);
        }
        return {
            subtitle: systems.length
                ? `Keeps ${joinList(systems)} and lets you pick which one to start`
                : "Keeps what is on it and uses the free space",
        };
    case "home-reuse":
        return { subtitle: "Replaces the system but keeps your home folder" };
    case "mount-point-mapping":
        if (availability?.hidden) {
            return reason("This drive has no partitions to assign yet");
        }
        if (availability && !availability.available && availability.reason) {
            return reason(availability.reason);
        }
        return { subtitle: "Assign mount points by hand" };
    default:
        return {};
    }
};

const TITLES = {
    "erase-all": "Use the whole drive",
    "home-reuse": "Reinstall Luma",
    "mount-point-mapping": "I will choose the partitions myself",
    "use-free-space": "Share with what is already there",
};

/* A safe default: never preselect erasing a drive that has something on it. */
const defaultScenario = ({ availability, row }) => {
    const usable = id => availability?.[id] && availability[id].available && !availability[id].hidden;
    if (!row) {
        return null;
    }
    const isEmpty = row.sentence.startsWith("Empty");
    if (isEmpty && usable("erase-all")) {
        return "erase-all";
    }
    if (usable("home-reuse")) {
        return "home-reuse";
    }
    if (usable("use-free-space")) {
        return "use-free-space";
    }
    return null;
};

export const DestinationStep = ({ answers, back, continueRef, dispatch, index, invalidateAfter, next, panelRef, setAnswers }) => {
    const { appliedPartitioning, diskSelection, partitioning, storageScenarioId } = useContext(StorageContext);
    const probe = useContext(ProbeContext);
    const devices = useOriginalDevices();
    const existingSystems = useOriginalExistingSystems();
    const requiredSize = useRequiredSize();
    const homeReuseOptions = useHomeReuseOptions();
    const availability = useScenariosAvailability();
    const [busy, setBusy] = useState(false);
    const [rescanning, setRescanning] = useState(false);
    const [problem, setProblem] = useState(null);
    const [scanned, setScanned] = useState(false);

    // As anaconda-webui's usePartitioningReset: opening this step discards a
    // plan made earlier (it is rebuilt on Continue). Asking Anaconda, not the
    // store, matters after a viewer reload, when the store has not heard of
    // the plan and would take the planned tree for the drives as they are.
    useEffect(() => {
        let cancelled = false;
        getAppliedPartitioning()
                .then(async path => {
                    if (path) {
                        await resetPartitioning();
                        await dispatch(getDevicesAction());
                        await dispatch(getDiskSelectionAction());
                    }
                })
                .catch(exception => logError("atlas: resetting an earlier plan failed", exception?.message))
                .finally(() => !cancelled && setScanned(true));
        return () => { cancelled = true };
    }, [dispatch]);

    // Remember every drive as first seen (see mergeDriveFacts).
    const disksSeen = [...diskSelection.usableDisks, ...(probe?.installerDisks || [])].join(",");
    useEffect(() => {
        if (!scanned || !Object.keys(devices).length) {
            return;
        }
        setAnswers(current => ({
            ...current,
            driveFacts: mergeDriveFacts(current.driveFacts, { devices, disks: disksSeen.split(",").filter(Boolean), existingSystems }),
        }));
    }, [devices, disksSeen, existingSystems, scanned, setAnswers]);

    const facts = answers.driveFacts || { devices: {}, existingSystems: [] };
    const factDevices = useMemo(() => ({ ...devices, ...facts.devices }), [devices, facts.devices]);

    const rows = useMemo(() => driveRows({
        devices: factDevices,
        existingSystems: facts.existingSystems.length ? facts.existingSystems : existingSystems,
        installerDisks: probe?.installerDisks || [],
        requiredSize,
        usableDisks: diskSelection.usableDisks,
    }), [factDevices, diskSelection.usableDisks, existingSystems, facts.existingSystems, probe?.installerDisks, requiredSize]);

    const selectable = rows.filter(row => !row.isInstaller && !row.disabledReason);
    const selectedDisk = diskSelection.selectedDisks.find(disk => selectable.some(row => row.disk === disk));
    const selectedRow = rows.find(row => row.disk === selectedDisk);
    const listProblem = driveListProblem({ requiredSize, rows });

    // One drive. Preselected only when there is nothing to lose by it: it is
    // the only drive to choose from, or the only one that is empty.
    useEffect(() => {
        const empty = selectable.filter(row => row.sentence.startsWith("Empty"));
        const wanted = selectedDisk
            ? [selectedDisk]
            : selectable.length === 1 ? [selectable[0].disk] : empty.length === 1 ? [empty[0].disk] : [];
        const currentSelection = diskSelection.selectedDisks;
        if (wanted.length !== currentSelection.length || wanted.some((disk, i) => disk !== currentSelection[i])) {
            setSelectedDisks({ drives: wanted }).catch(exception => logError("atlas: selecting disks failed", exception?.message));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectable.map(row => row.disk).join(","), selectedDisk]);

    // Keep the chosen method valid for the chosen drive.
    useEffect(() => {
        if (!availability || !selectedRow) {
            return;
        }
        const current = storageScenarioId && availability[storageScenarioId];
        if (current && current.available && !current.hidden) {
            return;
        }
        const fallback = defaultScenario({ availability, row: selectedRow });
        if (fallback !== storageScenarioId) {
            dispatch(setStorageScenarioAction(fallback));
        }
    }, [availability, dispatch, selectedRow, storageScenarioId]);

    const chooseDisk = (disk) => {
        if (disk === selectedDisk) {
            return;
        }
        invalidateAfter(index);
        setProblem(null);
        setSelectedDisks({ drives: [disk] }).catch(exception => {
            logError("atlas: selecting a disk failed", exception?.message);
            setProblem("Luma could not select that drive.");
        });
    };

    const chooseMethod = (id) => {
        if (id === storageScenarioId) {
            return;
        }
        invalidateAfter(index);
        dispatch(setStorageScenarioAction(id));
    };

    const rescan = async () => {
        setRescanning(true);
        setProblem(null);
        try {
            const task = await scanDevicesWithTask();
            await new Promise((resolve, reject) => runStorageTask({ onFail: reject, onSuccess: resolve, task }));
            await resetPartitioning();
            setAnswers(current => ({ ...current, driveFacts: null }));
            await Promise.all([dispatch(getDevicesAction()), dispatch(getDiskSelectionAction())]);
        } catch (exception) {
            logError("atlas: rescanning drives failed", exception?.message);
            setProblem("Luma could not look for drives again.");
        } finally {
            setRescanning(false);
        }
    };

    const scenarioAvailable = !!(storageScenarioId && availability?.[storageScenarioId]?.available && !availability[storageScenarioId].hidden);
    const valid = !!selectedDisk && scenarioAvailable && !busy && !rescanning;

    const onContinue = async () => {
        if (!valid) {
            return;
        }
        const scenario = scenarios.find(s => s.id === storageScenarioId);
        setBusy(true);
        setProblem(null);
        try {
            // Start every plan from the scanned drives, not from a previous plan.
            if (appliedPartitioning) {
                await resetPartitioning();
            }
            const path = scenario.method === "MANUAL"
                ? null
                : await getNewPartitioning({
                    currentPartitioning: partitioning,
                    homeReuseOptions,
                    method: scenario.method,
                    storageScenarioId,
                });
            setAnswers(current => ({ ...current, partitioningPath: path, scenarioMethod: scenario.method }));
            next();
        } catch (exception) {
            logError("atlas: preparing the drive plan failed", exception?.message);
            setProblem("Luma could not prepare a plan for this drive. Look for drives again, or pick another.");
        } finally {
            setBusy(false);
        }
    };
    continueRef.current = onContinue;

    const loading = !availability && diskSelection.usableDisks.length > 0;

    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="quiet" onClick={back}>Back</Button>
                  <Spacer />
                  <Button variant="primary" arrow busy={busy} disabled={!valid} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="disk"
          index={index}
          lede="Pick a drive, then tell Luma how much of it to use."
          panelRef={panelRef}
          title="Where should Luma live?"
        >
            <Group label="Choose a drive" labelId="disk-drives-label">
                <Options labelledBy="disk-drives-label">
                    {rows.map(row => (
                        <Option
                          capacity={row.isInstaller ? null : row.used}
                          dataFirst={row.disk === selectedDisk || (!selectedDisk && row === selectable[0])}
                          disabled={row.isInstaller || !!row.disabledReason}
                          glyph={<DriveGlyph kind={row.kind} />}
                          id={`disk-${row.disk}`}
                          key={row.disk}
                          onSelect={() => chooseDisk(row.disk)}
                          pressed={row.disk === selectedDisk}
                          subtitle={row.disabledReason && !row.isInstaller ? `${row.sentence} · ${row.disabledReason}` : row.sentence}
                          title={row.title}
                        />
                    ))}
                </Options>
                {listProblem && <Warn plain={listProblem.text} />}
                {(listProblem || rows.length === 0) && (
                    <MoreButton onClick={rescan}>{rescanning ? "Looking for drives" : "Look for drives again"}</MoreButton>
                )}
            </Group>
            {selectedRow && (
                <Group label="How should Luma use it?" labelId="disk-method-label">
                    {loading && <Loading>Checking the drive</Loading>}
                    {availability && (
                        <Options compact labelledBy="disk-method-label">
                            {scenarios
                                    .filter(scenario => !(scenario.id === "home-reuse" && availability[scenario.id]?.hidden))
                                    .filter(scenario => !(scenario.id === "use-free-space" && availability[scenario.id]?.hidden))
                                    .map(scenario => {
                                        const copy = methodCopy({
                                            availability: availability[scenario.id],
                                            bitlocker: selectedRow.bitlocker,
                                            id: scenario.id,
                                            systems: selectedRow.systems,
                                        });
                                        const unavailable = copy.disabled || !availability[scenario.id]?.available || availability[scenario.id]?.hidden;
                                        return (
                                            <Option
                                              disabled={unavailable}
                                              id={`disk-method-${scenario.id}`}
                                              key={scenario.id}
                                              onSelect={() => chooseMethod(scenario.id)}
                                              pressed={storageScenarioId === scenario.id}
                                              subtitle={copy.subtitle}
                                              title={TITLES[scenario.id]}
                                            />
                                        );
                                    })}
                        </Options>
                    )}
                </Group>
            )}
            {problem && <Warn live strong={problem} />}
        </Step>
    );
};

export const scenarioTitle = (id) => TITLES[id];
export const driveModelFor = (devices, disk) => v(devices[disk]?.description) || "";
