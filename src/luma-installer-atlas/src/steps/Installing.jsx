/*
 * Copyright (C) 2026 Project Luma contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Step 7 · Setting up Luma.
 * Boss.InstallWithTasks and the task's ProgressChanged / CategoryChanged /
 * Succeeded / Failed signals, as anaconda-webui 68 InstallationProgress.jsx
 * uses them, mapped onto four phases (model/progress.js).
 *
 * Two promises in the lede are kept honest:
 *  - duration: there is no payload-size estimate, so it says "usually under
 *    ten minutes" (handoff §3 allows exactly that wording) when Luma comes
 *    from the USB drive, and that it depends on the connection when Luma is
 *    downloaded (model/delivery.js);
 * The logind inhibitor still protects the running install from suspension.
 * After success the installer medium is ejected; step 8 appears only then.
 */
import React, { useContext, useEffect, useReducer, useRef, useState } from "react";

import { BossClient, getSteps, installWithTasks } from "../apis/boss.js";

import { exitGui } from "../helpers/exit.js";
import { error as logError } from "../helpers/log.js";
import { durationSentence } from "../model/delivery.js";
import { installationErrorDetails, missingInstallationError } from "../model/installation-error.js";
import { taskCompletion } from "../model/task-finish.js";
import {
    applyCategory, applyFailed, applyProgress, applySteps, applySucceeded, failureSentence, initialProgress,
    percent, PHASES, phaseStates, remember,
} from "../model/progress.js";

import { ProbeContext, StorageContext } from "../contexts/Common.jsx";

import { Actions, Button, Meter, MoreButton, Note, Phases, Spacer, Step, Warn } from "../atlas/components.jsx";
import { ejectMedium, holdSuspendInhibitor, saveLogs } from "../system/host.js";
import { loadSession, saveSession } from "../system/session.js";

const progressReducer = (state, action) => {
    switch (action.type) {
    case "category": return remember(applyCategory(state, action.category));
    case "progress": return remember(applyProgress(state, action.step, action.message));
    case "steps": return applySteps(state, action.steps);
    case "succeeded": return applySucceeded(state);
    case "failed": return applyFailed(state);
    default: return state;
    }
};

/* The install runs once per installer session, even if this step is left and
 * re-entered (step 8's Back). */
const session = { eject: null, errorDetails: null, inhibitor: null, progress: null, started: false, status: null };

const TASK = "org.fedoraproject.Anaconda.Task";

export const InstallingStep = ({ go, index, panelRef }) => {
    const { diskSelection } = useContext(StorageContext);
    const probe = useContext(ProbeContext);
    const [progress, dispatch] = useReducer(progressReducer, session.progress || initialProgress(), initial => {
        const saved = loadSession();
        return saved.installError ? {
            ...initial,
            failed: true,
            phase: Number.isInteger(saved.installFailurePhase) && saved.installFailurePhase >= 0 && saved.installFailurePhase < PHASES.length
                ? saved.installFailurePhase : initial.phase,
        } : initial;
    });
    const [status, setStatus] = useState(() => session.status || (loadSession().installError ? "failed" : "running"));
    const [eject, setEject] = useState(session.eject);
    const [logs, setLogs] = useState(null);
    const [errorDetails, setErrorDetails] = useState(() => session.errorDetails || loadSession().installError || null);
    const [showDetails, setShowDetails] = useState(false);
    const mounted = useRef(true);

    useEffect(() => {
        session.progress = progress;
    }, [progress]);
    useEffect(() => {
        session.status = status;
    }, [status]);
    useEffect(() => {
        if (status === "failed" && errorDetails) {
            // Persist after the reducer has processed this batch of category
            // signals; reportFailure's closure can still hold an older phase.
            const phase = Number.isInteger(progress.phase) && progress.phase >= 0 && progress.phase < PHASES.length
                ? progress.phase : 0;
            saveSession({ installError: errorDetails, installFailurePhase: phase });
        }
    }, [status, errorDetails, progress.phase]);

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false };
    }, []);

    const tryEject = async () => {
        if (loadSession().ejected) {
            session.eject = { state: "ejected" };
            setEject(session.eject);
            setTimeout(() => go(index + 1), 600);
            return;
        }
        setEject({ state: "ejecting" });
        let result;
        try {
            result = await ejectMedium();
        } catch (exception) {
            result = { ok: false, reason: exception?.message || "eject-failed" };
        }
        session.eject = result.ok ? { state: "ejected" } : { reason: result.reason, state: "failed" };
        if (result.ok) {
            saveSession({ ejected: true });
        }
        if (!mounted.current) {
            return;
        }
        setEject(session.eject);
        if (result.ok) {
            setTimeout(() => go(index + 1), 600);
        }
    };

    useEffect(() => {
        if (session.started) {
            return;
        }
        session.started = true;

        // Anaconda's Finish consumes the saved exception. A reload must not
        // call it again and mistake that earlier failed task for a success.
        if (loadSession().installError) {
            return;
        }

        holdSuspendInhibitor().then(result => {
            session.inhibitor = result;
        });

        const saved = loadSession().installTask;
        const reportFailure = (exception) => {
            session.errorDetails = installationErrorDetails(exception);
            saveSession({ installError: session.errorDetails });
            if (!mounted.current) {
                return;
            }
            setErrorDetails(session.errorDetails);
            dispatch({ type: "failed" });
            setStatus("failed");
        };
        const finish = (complete) => complete()
                .then(() => {
                    dispatch({ type: "succeeded" });
                    setStatus("succeeded");
                    session.inhibitor?.release();
                    tryEject();
                })
                .catch(reportFailure);

        // After a reload, follow the installation already running rather
        // than starting another one.
        const tasksPromise = saved
            ? Promise.resolve([saved])
            : installWithTasks().then(tasks => {
                saveSession({ installTask: tasks[0] });
                return tasks;
            });

        tasksPromise
                .then(tasks => {
                    const client = new BossClient().client;
                    const taskProxy = client.proxy(TASK, tasks[0]);
                    const complete = taskCompletion(taskProxy);
                    const categoryProxy = client.proxy("org.fedoraproject.Anaconda.TaskCategory", tasks[0]);

                    if (saved) {
                        client.call(tasks[0], "org.freedesktop.DBus.Properties", "GetAll", [TASK])
                                .then(([properties]) => {
                                    dispatch({ steps: properties.Steps?.v, type: "steps" });
                                    const [step, message] = properties.Progress?.v || [0, ""];
                                    dispatch({ message, step, type: "progress" });
                                    if (!properties.IsRunning?.v) {
                                        finish(complete);
                                    }
                                })
                                .catch(exception => logError("atlas: reading the running installation failed", exception?.message));
                    }

                    taskProxy.wait(() => {
                        taskProxy.addEventListener("ProgressChanged", (_event, step, message) => {
                            if (step === 0) {
                                getSteps({ task: tasks[0] }).then(value => dispatch({ steps: value.v, type: "steps" })).catch(() => {});
                            }
                            dispatch({ message, step, type: "progress" });
                        });
                        categoryProxy.addEventListener("CategoryChanged", (_event, category) => dispatch({ category, type: "category" }));
                        taskProxy.addEventListener("Failed", () => {
                            dispatch({ type: "failed" });
                            setStatus("failed");
                        });
                        taskProxy.addEventListener("Stopped", () => {
                            complete().catch(exception => {
                                logError("atlas: installation failed", exception?.message);
                                reportFailure(exception);
                            });
                        });
                        taskProxy.addEventListener("Succeeded", () => {
                            dispatch({ type: "succeeded" });
                            setStatus("succeeded");
                            session.inhibitor?.release();
                            tryEject();
                        });
                        if (saved) {
                            return;
                        }
                        getSteps({ task: tasks[0] }).then(value => dispatch({ steps: value.v, type: "steps" })).catch(() => {});
                        taskProxy.Start().catch(exception => {
                            logError("atlas: starting the installation failed", exception?.message);
                            reportFailure(exception);
                        });
                    });
                })
                .catch(exception => {
                    logError("atlas: InstallWithTasks failed", exception?.message);
                    reportFailure(exception);
                });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (status === "failed") {
            session.inhibitor?.release();
        }
    }, [status]);

    const value = percent(progress);
    const states = phaseStates(progress);
    const failed = status === "failed";
    const succeeded = status === "succeeded";
    const failure = failed ? failureSentence(progress) : null;

    const lede = durationSentence(probe?.media);

    const onSaveLogs = async () => {
        setLogs({ state: "saving" });
        try {
            const result = await saveLogs(diskSelection.selectedDisks);
            setLogs(result.ok
                ? { state: "saved", text: `Saved to ${result.label} as ${result.file}.` }
                : { state: "none", text: "Plug in a USB drive to save the log to, then try again." });
        } catch (exception) {
            setLogs({ state: "none", text: "The log could not be saved. Plug in a USB drive, then try again." });
        }
    };

    let actions = null;
    if (failed) {
        actions = (
            <Actions>
                <Button variant="quiet" busy={logs?.state === "saving"} onClick={onSaveLogs}>Save the log</Button>
                <Spacer />
                <Button variant="primary" onClick={exitGui}>Restart</Button>
            </Actions>
        );
    } else if (succeeded && eject?.state === "failed") {
        actions = (
            <Actions>
                <Button variant="quiet" onClick={tryEject}>Try again</Button>
                <Spacer />
                <Button variant="primary" onClick={exitGui}>Restart now</Button>
            </Actions>
        );
    } else if (succeeded && eject?.state === "ejected") {
        actions = (
            <Actions>
                <Spacer />
                <Button variant="primary" arrow onClick={() => go(index + 1)}>Continue</Button>
            </Actions>
        );
    }

    const ejectNote = succeeded && eject?.state === "failed"
        ? (eject.reason === "running-from-medium"
            ? "Luma is installed, but the installer is still running from the USB drive, so it cannot be ejected. Restart now, and take the drive out before the computer starts again."
            : "Luma is installed, but the USB drive could not be ejected. Try again. If it still will not eject, restart and take the drive out before the computer starts again.")
        : null;

    return (
        <Step
          actions={actions}
          id="installing"
          index={index}
          lede={lede}
          panelRef={panelRef}
          title="Setting up Luma."
        >
            <Meter label="Installation progress" value={value} />
            <p className="install-percent" aria-hidden="true">{value === null ? "" : `${value}%`}</p>
            <Phases labels={PHASES} states={states} />
            {failure && <Warn live strong={failure.strong} plain={failure.plain} />}
            {failed && (
                <>
                    <MoreButton
                      expanded={showDetails ? "true" : "false"}
                      controls="install-error-details"
                      onClick={() => setShowDetails(!showDetails)}
                    >
                        {showDetails ? "Hide details" : "Details"}
                    </MoreButton>
                    <div id="install-error-details" hidden={!showDetails} style={{ overflowWrap: "anywhere" }}>
                        <Note live>{errorDetails || missingInstallationError}</Note>
                    </div>
                </>
            )}
            {logs?.text && <Note live>{logs.text}</Note>}
            {succeeded && eject?.state === "ejecting" && <Note live>Ejecting the USB drive</Note>}
            {ejectNote && <Warn live plain={ejectNote} />}
        </Step>
    );
};
