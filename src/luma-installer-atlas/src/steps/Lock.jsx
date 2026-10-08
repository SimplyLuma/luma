/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 4 · Should we lock this drive?
 * LUKS2 through Anaconda's automatic partitioning request (encrypted +
 * passphrase), then the plan is configured, applied and validated exactly as
 * anaconda-webui 68 does (storage_partitioning.js applyStorage). Applying a
 * plan is still in memory: nothing is written until the review step.
 *
 * When the person chose to pick partitions by hand, this step is where they
 * do it (Fedora's order puts manual storage configuration here too).
 */
import cockpit from "cockpit";

import React, { useContext, useEffect, useMemo, useState } from "react";

import { applyStorage } from "../apis/storage_partitioning.js";

import { setLuksEncryptionDataAction } from "../actions/storage-actions.js";

import { error as logError } from "../helpers/log.js";
import { secretProblems } from "../model/account.js";

import { LanguageContext, RuntimeContext, StorageContext } from "../contexts/Common.jsx";

import {
    Actions, Button, Note, Pair, SecretField, Spacer, Step, SwitchRow, Warn,
} from "../atlas/components.jsx";
import { MountPoints } from "./MountPoints.jsx";

const v = (value) => (value && typeof value === "object" && "v" in value) ? value.v : value;

/** pwscore quality 0..100 for a secret, or null when it cannot be measured. */
export const usePasswordQuality = (secret) => {
    const [quality, setQuality] = useState(null);
    useEffect(() => {
        if (!secret) {
            setQuality(null);
            return;
        }
        let cancelled = false;
        const timer = setTimeout(() => {
            cockpit.spawn(["/usr/bin/pwscore"], { err: "message" })
                    .input(secret)
                    .then(output => !cancelled && setQuality(Number(output.trim())))
                    .catch(() => !cancelled && setQuality(0));
        }, 250);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [secret]);
    return quality;
};

/** Apply the current plan and return { errors, warnings }. */
export const applyPlan = ({ luks, partitioning }) => new Promise((resolve, reject) => {
    applyStorage({
        luks,
        onFail: reject,
        onSuccess: (report) => resolve({
            errors: v(report?.["error-messages"]) || [],
            warnings: v(report?.["warning-messages"]) || [],
        }),
        partitioning,
    }).catch(reject);
});

export const LockStep = (props) => {
    const { storageScenarioId } = useContext(StorageContext);
    if (storageScenarioId === "mount-point-mapping") {
        return <MountPoints {...props} />;
    }
    return <Lock {...props} />;
};

const Lock = ({ answers, back, continueRef, dispatch, index, invalidateAfter, next, panelRef, setAnswers }) => {
    const { luks, partitioning, storageScenarioId } = useContext(StorageContext);
    const { keyboardLayouts, plannedVconsole, plannedXlayouts } = useContext(LanguageContext);
    const policy = useContext(RuntimeContext).passwordPolicies?.luks;
    const [busy, setBusy] = useState(false);
    const [report, setReport] = useState(null);
    const [failure, setFailure] = useState(null);
    const [acceptedWarnings, setAcceptedWarnings] = useState(false);
    const reuse = storageScenarioId === "home-reuse";
    const planPath = answers.partitioningPath || partitioning?.path;

    // The reducer's fresh choice is unlocked. Keep any explicit backend or
    // restored user choice; mounting this step must never overwrite it.

    const quality = usePasswordQuality(luks.encrypted ? luks.passphrase : "");
    const checks = secretProblems({ confirm: luks.confirmPassphrase, noun: "passphrase", policy, quality, secret: luks.passphrase });

    const layoutName = useMemo(() => {
        const id = plannedXlayouts?.[0];
        const layout = keyboardLayouts.find(item => v(item["layout-id"]) === id);
        return v(layout?.description) || plannedVconsole || id;
    }, [keyboardLayouts, plannedVconsole, plannedXlayouts]);

    const change = (payload) => {
        if (typeof payload.encrypted === "boolean") {
            setAnswers(current => ({ ...current, lockChoice: payload.encrypted }));
        }
        invalidateAfter(index);
        setReport(null);
        setAcceptedWarnings(false);
        dispatch(setLuksEncryptionDataAction(payload));
    };

    const valid = !busy && !!planPath && (reuse || !luks.encrypted || checks.valid);

    const onContinue = async () => {
        if (!valid) {
            return;
        }
        if (report?.warnings.length && !report.errors.length && acceptedWarnings) {
            next();
            return;
        }
        setBusy(true);
        setFailure(null);
        try {
            const result = await applyPlan({
                luks: reuse ? undefined : { encrypted: luks.encrypted, passphrase: luks.encrypted ? luks.passphrase : "" },
                partitioning: planPath,
            });
            setReport(result);
            if (!result.errors.length && !result.warnings.length) {
                next();
            } else if (!result.errors.length) {
                setAcceptedWarnings(true);
            }
        } catch (exception) {
            logError("atlas: applying the drive plan failed", exception?.message);
            setFailure(exception?.message || "The drive plan could not be made.");
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
          lede="With the drive locked, your files are unreadable to anyone who takes the computer or pulls the drive out of it. You will type this passphrase every time the computer starts."
          panelRef={panelRef}
          title="Should we lock this drive?"
        >
            <SwitchRow
              checked={reuse ? false : luks.encrypted}
              dataFirst
              disabled={reuse}
              id="encryption-lock"
              onChange={encrypted => change({ encrypted })}
              subtitle={reuse ? "Reinstalling keeps the drive as it is, locked or not" : "Recommended for laptops"}
              title="Lock this drive"
            />
            {!reuse && luks.encrypted && (
                <>
                    <Pair>
                        <SecretField
                          id="encryption-passphrase"
                          label="Passphrase"
                          noun="passphrase"
                          notes={checks.notes}
                          onChange={passphrase => change({ passphrase })}
                          problem={checks.secretProblem}
                          value={luks.passphrase}
                        />
                        <SecretField
                          id="encryption-confirm"
                          label="Type it again"
                          noun="passphrase"
                          onChange={confirmPassphrase => change({ confirmPassphrase })}
                          onEnter={onContinue}
                          problem={checks.confirmProblem}
                          value={luks.confirmPassphrase}
                        />
                    </Pair>
                    {layoutName && (
                        <Note>
                            When the computer starts, you will type it with the <b>{layoutName}</b> keyboard layout.
                        </Note>
                    )}
                    <Warn
                      plain="If you forget the passphrase, the files on this drive are gone for good. Write it down somewhere safe before you continue."
                      strong="There is no way to recover this."
                    />
                </>
            )}
            {report?.errors.length > 0 && (
                <Warn live strong="Luma cannot use this plan.">
                    <ul>{report.errors.map(message => <li key={message}>{message}</li>)}</ul>
                </Warn>
            )}
            {report && !report.errors.length && report.warnings.length > 0 && (
                <Warn live strong="Check this before you continue." plain="Continue again to go ahead anyway.">
                    <ul>{report.warnings.map(message => <li key={message}>{message}</li>)}</ul>
                </Warn>
            )}
            {failure && <Warn live strong="Luma could not make a plan for this drive." plain={failure} />}
        </Step>
    );
};
