/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 4, by hand · Which partition goes where?
 *
 * NOT DESIGNED (handoff §9). This is Anaconda's mount point assignment
 * (anaconda-webui 68 MountPointMapping.jsx: MANUAL partitioning, gathered
 * requests, platform mount point constraints, reformat rules) presented with
 * Atlas's own components so the expert path is a real tool rather than a row
 * that opens nothing. It assigns existing partitions; it does not create,
 * resize or delete them.
 */
import React, { useContext, useEffect, useMemo, useState } from "react";

import { getMountPointConstraints } from "../apis/storage_devicetree.js";
import { gatherRequests, setManualPartitioningRequests } from "../apis/storage_partitioning.js";

import { error as logError } from "../helpers/log.js";
import { getDeviceAncestors, getDeviceChildren, requestsFromDbus, requestsToDbus } from "../helpers/storage.js";
import { formatSize, joinList } from "../model/format.js";

import { StorageContext } from "../contexts/Common.jsx";

import { getNewPartitioning, useOriginalDevices } from "../hooks/Storage.jsx";

import {
    Actions, Button, Loading, Note, SelectField, Spacer, Step, SwitchRow, Warn,
} from "../atlas/components.jsx";
import { applyPlan } from "./Lock.jsx";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

const USES = [
    { label: "Not used", value: "" },
    { label: "/ (the system)", value: "/" },
    { label: "/boot", value: "/boot" },
    { label: "/boot/efi", value: "/boot/efi" },
    { label: "/home", value: "/home" },
    { label: "/var", value: "/var" },
    { label: "swap", value: "swap" },
];

export const MountPoints = ({ answers, back, continueRef, index, invalidateAfter, next, panelRef, setAnswers }) => {
    const { diskSelection } = useContext(StorageContext);
    const devices = useOriginalDevices();
    const [partitioning, setPartitioning] = useState(answers.manualPath || null);
    const [requests, setRequests] = useState(answers.manualRequests || null);
    const [constraints, setConstraints] = useState([]);
    const [busy, setBusy] = useState(false);
    const [report, setReport] = useState(null);
    const [failure, setFailure] = useState(null);

    useEffect(() => {
        if (partitioning && requests) {
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const path = await getNewPartitioning({ method: "MANUAL", storageScenarioId: "mount-point-mapping" });
                const gathered = requestsFromDbus(await gatherRequests({ partitioning: path }));
                const limits = await getMountPointConstraints({ diskNames: diskSelection.selectedDisks });
                if (!cancelled) {
                    setPartitioning(path);
                    setRequests(gathered);
                    setConstraints(limits);
                }
            } catch (exception) {
                logError("atlas: reading partitions failed", exception?.message);
                !cancelled && setFailure("Luma could not read the partitions on this drive.");
            }
        })();
        return () => { cancelled = true };
    }, [diskSelection.selectedDisks, partitioning, requests]);

    useEffect(() => {
        if (!constraints.length && diskSelection.selectedDisks.length) {
            getMountPointConstraints({ diskNames: diskSelection.selectedDisks }).then(setConstraints).catch(() => {});
        }
    }, [constraints.length, diskSelection.selectedDisks]);

    const rows = useMemo(() => (requests || [])
            .filter(request => request["device-spec"] && devices[request["device-spec"]])
            .filter(request => {
                const ancestors = getDeviceAncestors(devices, request["device-spec"]);
                return diskSelection.selectedDisks.some(disk => [request["device-spec"], ...ancestors].includes(disk));
            }), [devices, diskSelection.selectedDisks, requests]);

    const update = (deviceSpec, change) => {
        invalidateAfter(index);
        setReport(null);
        setRequests(current => current.map(request => {
            if (request["device-spec"] !== deviceSpec) {
                return request;
            }
            const nextRequest = { ...request, ...change };
            if (change["mount-point"] !== undefined) {
                nextRequest["mount-point"] = change["mount-point"] === "swap" ? "" : change["mount-point"];
                nextRequest["format-type"] = change["mount-point"] === "swap" ? "swap" : request["format-type"];
                if (change["mount-point"] === "/") {
                    nextRequest.reformat = true;
                }
            }
            return nextRequest;
        }));
    };

    const assigned = rows.filter(request => request["mount-point"] || request["format-type"] === "swap" && request.reformat);
    const mounts = assigned.map(request => request["mount-point"]).filter(Boolean);
    const duplicates = mounts.filter((mount, i) => mounts.indexOf(mount) !== i);
    const missing = constraints
            .filter(constraint => v(constraint.required) && v(constraint["mount-point"]) && !mounts.includes(v(constraint["mount-point"])))
            .map(constraint => v(constraint["mount-point"]));
    const reformatConflicts = rows.filter(request => {
        if (!request.reformat) {
            return false;
        }
        return getDeviceChildren({ device: request["device-spec"], deviceData: devices })
                .some(child => rows.find(r => r["device-spec"] === child && (r["mount-point"] && !r.reformat)));
    });

    const problems = [
        missing.length ? `Choose a partition for ${joinList(missing)}.` : null,
        duplicates.length ? `${joinList([...new Set(duplicates)])} is assigned twice.` : null,
        reformatConflicts.length ? "A partition you are formatting holds another partition you are keeping. Format both, or neither." : null,
    ].filter(Boolean);

    const valid = !!partitioning && !!requests && !busy && problems.length === 0 && mounts.includes("/");

    const onContinue = async () => {
        if (!valid) {
            return;
        }
        setBusy(true);
        setFailure(null);
        try {
            await setManualPartitioningRequests({ partitioning, requests: requestsToDbus(requests) });
            const result = await applyPlan({ partitioning });
            setReport(result);
            if (!result.errors.length) {
                setAnswers(current => ({
                    ...current,
                    manualPath: partitioning,
                    manualRequests: requests,
                    partitioningPath: partitioning,
                    reformatted: requests.filter(r => r.reformat && r["mount-point"]).map(r => r["mount-point"]),
                }));
                next();
            }
        } catch (exception) {
            logError("atlas: applying mount points failed", exception?.message);
            setFailure(exception?.message || "The assignment could not be used.");
        } finally {
            setBusy(false);
        }
    };
    continueRef.current = onContinue;

    return (
        <Step
          actions={(
              <Actions>
                  <Button variant="quiet" onClick={back}>Back</Button>
                  <Spacer />
                  <Button variant="primary" arrow busy={busy} disabled={!valid} onClick={onContinue}>Continue</Button>
              </Actions>
          )}
          id="encryption"
          index={index}
          lede="Say what each partition is for. Luma only formats the partitions you switch on, and never changes their size."
          panelRef={panelRef}
          title="Which partition goes where?"
        >
            {!requests && !failure && <Loading>Reading the partitions</Loading>}
            {rows.map((request, position) => {
                const device = devices[request["device-spec"]];
                const format = v(device?.formatData?.type) || "no file system";
                const use = request["format-type"] === "swap" && !request["mount-point"] ? "swap" : (request["mount-point"] || "");
                const idBase = `mount-${request["device-spec"].replace(/\W/g, "-")}`;
                return (
                    <div className="install-group" key={request["device-spec"]} role="group" aria-labelledby={`${idBase}-name`}>
                        <p className="install-grouplabel" id={`${idBase}-name`}>
                            {`${v(device?.name)} · ${formatSize(v(device?.size))} · ${format}`}
                        </p>
                        <SelectField
                          dataFirst={position === 0}
                          id={`${idBase}-use`}
                          label="Use it as"
                          onChange={value => update(request["device-spec"], { "mount-point": value })}
                          options={USES}
                          value={use}
                        />
                        {use && (
                            <SwitchRow
                              checked={!!request.reformat}
                              disabled={use === "/"}
                              id={`${idBase}-format`}
                              onChange={reformat => update(request["device-spec"], { reformat })}
                              subtitle={request.reformat ? "Erases what is on this partition" : "Keeps what is on this partition"}
                              title="Format it"
                            />
                        )}
                    </div>
                );
            })}
            {requests && rows.length === 0 && <Note>This drive has no partitions Luma can use. Go back and pick another way.</Note>}
            {problems.length > 0 && <Warn plain={problems.join(" ")} />}
            {report?.errors.length > 0 && (
                <Warn live strong="Luma cannot use this assignment.">
                    <ul>{report.errors.map(message => <li key={message}>{message}</li>)}</ul>
                </Warn>
            )}
            {failure && <Warn live strong="Something went wrong." plain={failure} />}
        </Step>
    );
};
